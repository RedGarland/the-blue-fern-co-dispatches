from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.adapters.care_line import (
    ADAPTER_VERSION,
    deterministic_json,
    ingest_care_line_shadow,
    main,
    markdown_report,
)
from bluefern_dispatches.universal_events.orm import (
    CandidateEventRow,
    EntityMatchCandidateRow,
    EntityMentionRow,
    EntityResolutionDecisionRow,
    EventRow,
    SourceItemRow,
    SourceRow,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "care_line_shadow_records.json"


@pytest.fixture()
def service(tmp_path: Path) -> UniversalEventService:
    repo = SQLiteUniversalEventRepository(tmp_path / "care-line-shadow.sqlite3")
    repo.initialize_schema()
    return UniversalEventService(repo)


def records() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def record(record_id: str) -> dict:
    return next(row for row in records() if row["source_record_id"] == record_id)


def count_rows(service: UniversalEventService, row_cls) -> int:
    with service.repository.session_scope() as session:
        return len(session.execute(select(row_cls)).scalars().all())


def candidates(service: UniversalEventService) -> list[CandidateEventRow]:
    with service.repository.session_scope() as session:
        return list(session.execute(select(CandidateEventRow).order_by(CandidateEventRow.candidate_id)).scalars().all())


def mentions(service: UniversalEventService) -> list[EntityMentionRow]:
    with service.repository.session_scope() as session:
        return list(session.execute(select(EntityMentionRow).order_by(EntityMentionRow.mention_id)).scalars().all())


def test_eligible_care_line_record_becomes_source_item_and_candidate(service: UniversalEventService):
    report = ingest_care_line_shadow([record("care-shadow-001-ld-closure")], service)

    assert report["run_summary"]["eligible_count"] == 1
    assert report["run_summary"]["created_source_count"] == 1
    assert report["run_summary"]["created_source_item_count"] == 1
    assert report["run_summary"]["created_candidate_count"] == 1
    assert count_rows(service, SourceRow) == 1
    assert count_rows(service, SourceItemRow) == 1
    assert count_rows(service, CandidateEventRow) == 1
    candidate = candidates(service)[0]
    assert candidate.domain.value == "healthcare_access"
    assert candidate.candidate_status.value == "needs_review"
    assert candidate.metadata_json["producer"] == "Care Line"
    assert candidate.metadata_json["event_type"] == "service_closure"


def test_non_operational_healthcare_story_is_excluded(service: UniversalEventService):
    report = ingest_care_line_shadow([record("care-shadow-007-financial-only")], service)

    assert report["excluded_records"] == [
        {
            "producer_record_id": "care-shadow-007-financial-only",
            "reason": "financial_context_only",
            "source_url": "https://example.test/bond-rating",
        }
    ]
    assert count_rows(service, CandidateEventRow) == 0


def test_missing_source_url_is_excluded(service: UniversalEventService):
    report = ingest_care_line_shadow([record("care-shadow-015-missing-source")], service)

    assert report["excluded_records"][0]["reason"] == "missing_source_url"
    assert count_rows(service, SourceRow) == 0


def test_candidate_id_is_stable_across_title_correction(service: UniversalEventService):
    original = record("care-shadow-013-corrected")
    corrected = dict(original)
    corrected["title"] = "Oak Bend Hospital corrects oncology reduction date"
    corrected["effective_date"] = "2026-07-15"

    first = ingest_care_line_shadow([original], service)
    second = ingest_care_line_shadow([corrected], service)

    assert first["created_candidates"][0]["candidate_id"] == second["existing_candidates"][0]["candidate_id"]
    candidate = candidates(service)[0]
    versions = candidate.metadata_json["producer_payload_versions"]
    assert len(versions) == 2
    assert {version["title"] for version in versions} == {
        "Oak Bend Hospital reduces oncology appointments",
        "Oak Bend Hospital corrects oncology reduction date",
    }


def test_repeated_ingestion_is_idempotent(service: UniversalEventService):
    rows = records()
    first = ingest_care_line_shadow(rows, service)
    counts_after_first = {
        "sources": count_rows(service, SourceRow),
        "source_items": count_rows(service, SourceItemRow),
        "candidates": count_rows(service, CandidateEventRow),
        "mentions": count_rows(service, EntityMentionRow),
        "matches": count_rows(service, EntityMatchCandidateRow),
        "events": count_rows(service, EventRow),
    }
    second = ingest_care_line_shadow(rows, service)

    assert first["run_summary"]["created_candidate_count"] == 12
    assert second["run_summary"]["created_candidate_count"] == 0
    assert second["run_summary"]["existing_candidate_count"] == 12
    assert counts_after_first == {
        "sources": count_rows(service, SourceRow),
        "source_items": count_rows(service, SourceItemRow),
        "candidates": count_rows(service, CandidateEventRow),
        "mentions": count_rows(service, EntityMentionRow),
        "matches": count_rows(service, EntityMatchCandidateRow),
        "events": count_rows(service, EventRow),
    }


def test_source_registration_and_source_item_creation_are_idempotent(service: UniversalEventService):
    row = record("care-shadow-001-ld-closure")

    ingest_care_line_shadow([row], service)
    ingest_care_line_shadow([row], service)

    assert count_rows(service, SourceRow) == 1
    assert count_rows(service, SourceItemRow) == 1


def test_organization_mentions_preserve_raw_names(service: UniversalEventService):
    ingest_care_line_shadow([record("care-shadow-004-ownership")], service)

    org_mentions = [mention for mention in mentions(service) if mention.entity_kind == "organization"]
    assert ("facility", "Lakeside Hospital") in {(row.mention_role, row.raw_name) for row in org_mentions}
    assert ("former_owner", "Community Care Trust") in {(row.mention_role, row.raw_name) for row in org_mentions}
    assert ("new_owner", "Harbor Health System") in {(row.mention_role, row.raw_name) for row in org_mentions}
    assert ("regulator", "State Health Department") in {(row.mention_role, row.raw_name) for row in org_mentions}


def test_location_mentions_preserve_administrative_hierarchy(service: UniversalEventService):
    ingest_care_line_shadow([record("care-shadow-001-ld-closure")], service)

    location_mentions = [mention for mention in mentions(service) if mention.entity_kind == "location"]
    city = next(row for row in location_mentions if row.mention_role == "city")
    county = next(row for row in location_mentions if row.mention_role == "county")
    state = next(row for row in location_mentions if row.mention_role == "state")
    assert city.raw_name == "Cedar Falls"
    assert city.region == "IA"
    assert county.raw_name == "Black Hawk County"
    assert state.raw_name == "IA"


def test_exact_cms_identifier_produces_expected_match_candidate(service: UniversalEventService):
    org = service.create_organization({"organization_id": "org-silver-lake", "canonical_name": "Silver Lake Hospital", "organization_type": "hospital"})
    service.add_organization_identifier(
        {
            "organization_id": org.organization_id,
            "identifier_scheme": "cms_ccn",
            "identifier_value": "123456",
            "is_authoritative": True,
        }
    )

    report = ingest_care_line_shadow([record("care-shadow-010-cms-exact")], service)

    assert report["automatic_match_candidates"]
    assert report["automatic_match_candidates"][0]["target_id"] == "org-silver-lake"
    with service.repository.session_scope() as session:
        match = session.execute(select(EntityMatchCandidateRow).where(EntityMatchCandidateRow.organization_id == "org-silver-lake")).scalars().first()
        assert match is not None
        assert match.match_method == "exact_identifier"
        assert match.resolver_version


def test_similar_facility_names_in_different_cities_remain_distinct(service: UniversalEventService):
    service.create_location({"location_id": "loc-il", "canonical_name": "Springfield Clinic", "city": "Springfield", "state": "IL", "country_code": "US"})
    service.create_location({"location_id": "loc-ma", "canonical_name": "Springfield Clinic", "city": "Springfield", "state": "MA", "country_code": "US"})

    ingest_care_line_shadow([record("care-shadow-009-ambiguous")], service)

    location_mentions = [row for row in mentions(service) if row.entity_kind == "location"]
    assert {row.region for row in location_mentions} == {"IL"}


def test_health_system_and_facility_are_not_collapsed(service: UniversalEventService):
    ingest_care_line_shadow([record("care-shadow-011-system-facility-a"), record("care-shadow-012-system-facility-b")], service)

    org_mentions = {(row.mention_role, row.raw_name) for row in mentions(service) if row.entity_kind == "organization"}
    assert ("facility", "Eastside Clinic") in org_mentions
    assert ("facility", "Westside Clinic") in org_mentions
    assert ("operator", "Northstar Health") in org_mentions
    assert count_rows(service, EntityResolutionDecisionRow) == 0


def test_ambiguous_facility_remains_unresolved(service: UniversalEventService):
    first = service.create_organization({"organization_id": "org-river-a", "canonical_name": "North River Clinic", "organization_type": "clinic"})
    second = service.create_organization({"organization_id": "org-river-b", "canonical_name": "South River Clinic", "organization_type": "clinic"})
    service.add_organization_alias({"organization_id": first.organization_id, "alias_name": "River Clinic"})
    service.add_organization_alias({"organization_id": second.organization_id, "alias_name": "River Clinic"})

    report = ingest_care_line_shadow([record("care-shadow-009-ambiguous")], service)

    assert report["ambiguous_matches"]
    assert count_rows(service, EntityResolutionDecisionRow) == 0


def test_unknown_event_type_does_not_silently_map(service: UniversalEventService):
    report = ingest_care_line_shadow([record("care-shadow-016-unsupported")], service)

    assert report["excluded_records"][0]["reason"] == "unsupported_event_type"
    assert count_rows(service, CandidateEventRow) == 0


def test_service_line_is_stored_as_candidate_attribute(service: UniversalEventService):
    ingest_care_line_shadow([record("care-shadow-005-behavioral-reduction")], service)

    attrs = candidates(service)[0].metadata_json["healthcare_attributes"]
    assert attrs["service_line"] == "Behavioral health"
    assert attrs["service_line_normalized"] == "behavioral_health"
    assert attrs["licensed_beds_before"] == 40
    assert attrs["licensed_beds_after"] == 24


def test_facility_relocation_creates_old_new_location_context(service: UniversalEventService):
    ingest_care_line_shadow([record("care-shadow-003-clinic-relocation")], service)

    candidate = candidates(service)[0]
    assert candidate.metadata_json["healthcare_attributes"]["effective_date_text"] == "2026-06-20"
    location_mentions = {(row.mention_role, row.raw_name) for row in mentions(service) if row.entity_kind == "location"}
    assert ("facility_address", "Maple Street Clinic") in location_mentions
    assert ("replacement_service_location", "90 Oak Avenue, Dayton, OH") in location_mentions


def test_withdrawal_does_not_hard_delete_candidate_history(service: UniversalEventService):
    original = record("care-shadow-006-reopening")
    withdrawn = dict(original)
    withdrawn["withdrawn"] = True
    withdrawn["summary_or_snippet"] = "This reopening notice was withdrawn pending correction."

    ingest_care_line_shadow([original], service)
    ingest_care_line_shadow([withdrawn], service)

    candidate = candidates(service)[0]
    assert candidate.metadata_json["shadow_withdrawn"] is True
    assert len(candidate.metadata_json["producer_payload_versions"]) == 2
    assert count_rows(service, CandidateEventRow) == 1


def test_duplicate_source_coverage_remains_traceable(service: UniversalEventService):
    ingest_care_line_shadow([record("care-shadow-013-corrected"), record("care-shadow-014-duplicate-coverage")], service)

    assert count_rows(service, CandidateEventRow) == 2
    duplicate = next(row for row in candidates(service) if row.metadata_json["producer_record_id"] == "care-shadow-014-duplicate-coverage")
    assert duplicate.metadata_json["duplicate_of_producer_record_id"] == "care-shadow-013-corrected"


def test_adapter_does_not_create_verified_events_or_resolution_decisions_by_default(service: UniversalEventService):
    ingest_care_line_shadow(records(), service)

    assert count_rows(service, EventRow) == 0
    assert count_rows(service, EntityResolutionDecisionRow) == 0


def test_adapter_does_not_modify_care_line_records(service: UniversalEventService):
    rows = records()
    before = json.loads(json.dumps(rows, sort_keys=True))

    ingest_care_line_shadow(rows, service)

    assert rows == before


def test_adapter_does_not_write_public_output(service: UniversalEventService, tmp_path: Path):
    public_root = tmp_path / "output" / "site"
    before = sorted(public_root.rglob("*")) if public_root.exists() else []

    ingest_care_line_shadow([record("care-shadow-001-ld-closure")], service)

    after = sorted(public_root.rglob("*")) if public_root.exists() else []
    assert after == before == []


def test_check_only_performs_no_database_writes(service: UniversalEventService):
    report = ingest_care_line_shadow(records(), service, check_only=True)

    assert report["run_summary"]["eligible_count"] == 12
    assert report["run_summary"]["created_candidate_count"] == 0
    assert count_rows(service, SourceRow) == 0
    assert count_rows(service, CandidateEventRow) == 0


def test_shadow_report_ordering_and_json_are_deterministic(service: UniversalEventService):
    first = ingest_care_line_shadow(records(), service, check_only=True)
    second = ingest_care_line_shadow(list(reversed(records())), service, check_only=True)

    assert deterministic_json(first) == deterministic_json(second)
    assert "run_summary" in deterministic_json(first)
    assert markdown_report(first).startswith("# Care Line Universal Events Shadow Ingestion")


def test_repeated_execution_creates_no_duplicate_mentions_or_match_candidates(service: UniversalEventService):
    org = service.create_organization({"organization_id": "org-silver-lake", "canonical_name": "Silver Lake Hospital", "organization_type": "hospital"})
    service.add_organization_identifier({"organization_id": org.organization_id, "identifier_scheme": "cms_ccn", "identifier_value": "123456", "is_authoritative": True})

    ingest_care_line_shadow([record("care-shadow-010-cms-exact")], service)
    first_counts = (count_rows(service, EntityMentionRow), count_rows(service, EntityMatchCandidateRow))
    ingest_care_line_shadow([record("care-shadow-010-cms-exact")], service)

    assert (count_rows(service, EntityMentionRow), count_rows(service, EntityMatchCandidateRow)) == first_counts


def test_cli_refuses_missing_database_path():
    with pytest.raises(SystemExit):
        main(["--input", str(FIXTURE), "--shadow", "--report", "report.json"])


def test_cli_refuses_pages_repository_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pages = tmp_path / "bluefern-dispatches-pages"
    pages.mkdir()
    monkeypatch.chdir(tmp_path)

    code = main(
        [
            "--database",
            str(tmp_path / "shadow.sqlite3"),
            "--input",
            str(FIXTURE),
            "--shadow",
            "--report",
            str(pages / "report.json"),
        ]
    )

    assert code == 1
    assert not (pages / "report.json").exists()


def test_cli_check_only_writes_report_without_database_rows(tmp_path: Path):
    db = tmp_path / "shadow.sqlite3"
    report = tmp_path / "report.json"
    md = tmp_path / "report.md"

    code = main(["--database", str(db), "--input", str(FIXTURE), "--shadow", "--check-only", "--report", str(report), "--markdown-report", str(md)])

    assert code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["run_summary"]["check_only"] is True
    assert md.read_text(encoding="utf-8").startswith("# Care Line Universal Events Shadow Ingestion")
    repo = SQLiteUniversalEventRepository(db)
    repo.initialize_schema()
    service = UniversalEventService(repo)
    assert count_rows(service, CandidateEventRow) == 0
    repo.close()


def test_cli_module_invocation_creates_shadow_report(tmp_path: Path):
    db = tmp_path / "shadow.sqlite3"
    report = tmp_path / "shadow-report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bluefern_dispatches.universal_events.adapters.care_line",
            "--database",
            str(db),
            "--input",
            str(FIXTURE),
            "--shadow",
            "--report",
            str(report),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["run_summary"]["adapter_version"] == ADAPTER_VERSION
    assert payload["run_summary"]["eligible_count"] == 12
    assert payload["run_summary"]["excluded_count"] == 4
