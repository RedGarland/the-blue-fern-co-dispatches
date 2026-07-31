import base64
import hashlib
import json
from pathlib import Path

import pytest

from bluefern_dispatches.historical_agent_archive import normalize_records
from bluefern_dispatches.ice_historical import (
    ICE_EVENT_CATEGORIES,
    ICE_SCHEMA_FIELDS,
    extract_detection_date,
    normalize_detection_date,
)
from scripts.import_historical_agent_runs import main


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def finding(**extra) -> dict:
    value = {
        "finding_id": "ice-finding-1",
        "source_url": "https://www.ice.gov/news/releases/fixture-incident",
        "canonical_source_url": "https://www.ice.gov/news/releases/fixture-incident",
        "publisher": "U.S. Immigration and Customs Enforcement",
        "source_published_at": "2026-07-02",
        "event_date": "2026-07-01",
        "title": "Historical ICE incident",
        "exact_supporting_passage": "The agency reported a documented immigration-enforcement development on July 1.",
        "summary": "A private historical finding retained for review.",
        "event_category": "enforcement_operation",
        "location_name": "El Paso",
        "city": "El Paso",
        "county": "El Paso County",
        "state_or_territory": "Texas",
        "facility_name": None,
        "agency": "U.S. Immigration and Customs Enforcement",
        "affected_population": None,
        "fatalities": None,
        "serious_injuries": None,
        "hospitalizations": None,
        "enforcement_activity": True,
        "evidence_level": "primary_report",
        "confidence": "high",
        "verification_status": "pending_review",
        "raw_finding_reference": "finding-1",
    }
    value.update(extra)
    return value


def envelope(*rows: dict) -> dict:
    return {
        "schema_version": 1,
        "agent_name": "ICE Historical Source Watch",
        "agent_run_id": "ice-run-1",
        "started_at": "2026-07-03T00:00:00Z",
        "findings": list(rows),
    }


def normalize(root: Path, row: dict) -> tuple[dict, dict]:
    records, outcomes = normalize_records(
        root,
        "ice",
        envelope(row),
        raw_sha256="raw-sha",
        captured_at="2026-07-30T00:00:00Z",
    )
    return records[0], outcomes


def run_json(capsys, args: list[str]) -> tuple[int, dict]:
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


def sidecar(root: Path, raw_path: Path, *, overrides: dict | None = None) -> Path:
    row = finding(
        source_url="https://www.ice.gov/news/releases/prose-incident",
        canonical_source_url="https://www.ice.gov/news/releases/prose-incident",
        source_published_at="2026-07-02",
        event_date="2026-07-01",
        exact_supporting_passage="ICE reported one person was hospitalized in El Paso on July 1, 2026.",
        event_category="hospitalization",
        hospitalizations=1,
        location_name="El Paso",
        city="El Paso",
        county=None,
        agency="U.S. Immigration and Customs Enforcement",
    )
    value = {
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "raw_file": raw_path.relative_to(root).as_posix(),
        "domain": "ice",
        "normalization_type": "prose_envelope_to_structured_findings",
        "reviewer": "fixture",
        "reviewed_at": "2026-07-30T00:00:00Z",
        "approved": True,
        "approval_scope": "historical_normalization_only",
        "publication_approval": False,
        "findings": [row],
    }
    value.update(overrides or {})
    path = raw_path.parent / "corrections" / "structured.json"
    write_json(path, value)
    return path


def test_ice_contract_contains_every_required_field():
    expected = {
        "finding_id", "agent_name", "agent_run_id", "source_url", "canonical_source_url",
        "publisher", "source_published_at", "event_date", "detection_date", "title", "exact_supporting_passage",
        "summary", "event_category", "event_subtype", "severity", "location_name", "city",
        "county", "state_or_territory", "facility_name", "agency", "affected_population",
        "fatalities", "serious_injuries", "hospitalizations", "detention_activity",
        "removal_activity", "enforcement_activity", "use_of_force", "legal_action",
        "policy_action", "investigation", "community_impact", "evidence_level", "confidence",
        "verification_status", "historical_backfill", "review_status", "publication_eligible",
        "publication_approval", "exclusion_reason", "raw_finding_reference",
    }
    assert set(ICE_SCHEMA_FIELDS) == expected


def test_controlled_event_taxonomy_contains_required_categories():
    required = {
        "enforcement_operation", "arrest_or_apprehension", "detention_transfer",
        "detention_capacity_change", "detention_facility_opening", "detention_facility_closure",
        "detention_overcrowding", "removal_or_deportation", "removal_flight",
        "death_in_custody", "serious_injury", "hospitalization", "medical_emergency",
        "suicide_or_self_harm", "shooting_or_firearm_discharge", "taser_use",
        "physical_force", "pursuit", "tactical_deployment", "delayed_or_denied_care",
        "legal_ruling", "lawsuit_or_settlement", "civil_rights_investigation",
        "misconduct_investigation", "policy_change", "287g_action",
        "sanctuary_or_local_response", "demonstration_or_community_disruption",
        "workforce_or_business_disruption", "school_or_agricultural_disruption",
        "humanitarian_response", "archived_context",
    }
    assert required <= ICE_EVENT_CATEGORIES


def test_death_in_custody_is_critical_without_inventing_counts(tmp_path: Path):
    record, outcomes = normalize(tmp_path, finding(
        event_category="death_in_custody",
        exact_supporting_passage="The county coroner and agency reported that a person died while in ICE custody.",
        fatalities=None,
    ))
    assert outcomes == {"new_historical_candidate": 1}
    assert record["severity"] == "critical"
    assert record["fatalities"] is None
    assert record["publication_eligible"] is False


def test_nonfatal_hospitalization_is_high_without_inventing_other_details(tmp_path: Path):
    record, _ = normalize(tmp_path, finding(
        event_category="hospitalization",
        exact_supporting_passage="The agency reported that one detained person was transported to a hospital.",
        hospitalizations=1,
        fatalities=None,
        serious_injuries=None,
    ))
    assert record["severity"] == "high"
    assert record["hospitalizations"] == 1
    assert record["fatalities"] is None
    assert record["serious_injuries"] is None


def test_overcrowding_requires_documented_major_scope_for_high_severity(tmp_path: Path):
    ordinary, _ = normalize(tmp_path, finding(
        event_category="detention_overcrowding",
        exact_supporting_passage="The inspection report documented overcrowding at the detention facility.",
    ))
    major, _ = normalize(tmp_path, finding(
        finding_id="major-overcrowding",
        event_category="detention_overcrowding",
        event_subtype="major_facility_overcrowding",
        exact_supporting_passage="The inspection report documented major overcrowding at the detention facility.",
    ))
    assert ordinary["severity"] is None
    assert major["severity"] == "high"


def test_use_of_force_preserves_qualified_language(tmp_path: Path):
    passage = "The complaint alleges that officers used physical force; the allegation has not been adjudicated."
    record, _ = normalize(tmp_path, finding(
        event_category="physical_force",
        exact_supporting_passage=passage,
        use_of_force="alleged_in_complaint",
        evidence_level="filed_complaint",
        confidence="moderate",
    ))
    assert record["exact_supporting_passage"] == passage
    assert record["use_of_force"] == "alleged_in_complaint"
    assert record["severity"] is None


def test_removal_flight_preserves_date_and_destination(tmp_path: Path):
    record, _ = normalize(tmp_path, finding(
        event_category="removal_flight",
        event_date="2026-06-28",
        removal_destination="Guatemala City, Guatemala",
        removal_activity=True,
        exact_supporting_passage="The agency flight log lists a June 28 removal flight to Guatemala City.",
    ))
    assert record["event_date"] == "2026-06-28"
    assert record["removal_destination"] == "Guatemala City, Guatemala"
    assert record["event_category"] == "removal_flight"


def test_detention_capacity_change_normalizes_without_public_eligibility(tmp_path: Path):
    record, _ = normalize(tmp_path, finding(
        event_category="detention_capacity_change",
        facility_name="South Texas Family Residential Center",
        detention_activity="documented_capacity_change",
        exact_supporting_passage="The contract amendment documents a change in the facility's funded detention capacity.",
    ))
    assert record["facility_name"] == "South Texas Family Residential Center"
    assert record["detention_activity"] == "documented_capacity_change"
    assert record["publication_eligible"] is False


def test_legal_record_matches_exact_docket_identifier(tmp_path: Path):
    write_json(tmp_path / "data/dispatches/ice/legal/case.json", {
        "legal_record_id": "legal-1",
        "docket_number": "1:26-cv-00123",
        "source_url": "https://www.courtlistener.com/docket/ice-123",
        "event_date": "2026-07-01",
        "event_category": "legal_ruling",
    })
    record, outcomes = normalize(tmp_path, finding(
        event_category="legal_ruling",
        docket_number="1:26-cv-00123",
        event_date="2026-07-01",
        source_url="https://www.courtlistener.com/docket/ice-123",
        canonical_source_url="https://www.courtlistener.com/docket/ice-123",
        exact_supporting_passage="The court docket records an order entered on July 1.",
        legal_action=True,
    ))
    assert outcomes == {"matched_existing_legal_record": 1}
    assert record["matched_record_id"] == "legal-1"
    assert record["candidate_created"] is False
    assert record["provenance_only"] is True


def test_287g_and_sanctuary_responses_remain_distinct(tmp_path: Path):
    agreement, _ = normalize(tmp_path, finding(
        finding_id="agreement",
        event_category="287g_action",
        secondary_event_categories=["policy_change"],
        exact_supporting_passage="The sheriff signed a documented 287(g) agreement with ICE.",
        policy_action=True,
    ))
    response, _ = normalize(tmp_path, finding(
        finding_id="response",
        source_url="https://city.gov/sanctuary-response",
        canonical_source_url="https://city.gov/sanctuary-response",
        event_category="sanctuary_or_local_response",
        exact_supporting_passage="The city council adopted a local response limiting municipal participation.",
        policy_action=True,
    ))
    assert agreement["event_category"] == "287g_action"
    assert agreement["secondary_event_categories"] == ["policy_change"]
    assert response["event_category"] == "sanctuary_or_local_response"


def test_community_impact_remains_separate_from_enforcement_fact(tmp_path: Path):
    record, _ = normalize(tmp_path, finding(
        event_category="enforcement_operation",
        enforcement_activity={"status": "reported_operation"},
        community_impact={"status": "reported_school_absences", "source": "district statement"},
        exact_supporting_passage="ICE confirmed an operation; a separate district statement reported increased absences.",
    ))
    assert record["enforcement_activity"] == {"status": "reported_operation"}
    assert record["community_impact"] == {"status": "reported_school_absences", "source": "district statement"}


def test_existing_incident_match_creates_provenance_only(tmp_path: Path):
    write_json(tmp_path / "data/dispatches/ice/incidents/incident.json", {
        "incident_id": "incident-123",
        "source_url": "https://www.ice.gov/news/releases/incident-123",
        "event_date": "2026-07-01",
        "event_category": "enforcement_operation",
        "location_name": "El Paso",
        "agency": "U.S. Immigration and Customs Enforcement",
    })
    record, outcomes = normalize(tmp_path, finding(
        incident_id="incident-123",
        source_url="https://www.ice.gov/news/releases/incident-123",
        canonical_source_url="https://www.ice.gov/news/releases/incident-123",
    ))
    assert outcomes == {"matched_existing_incident": 1}
    assert record["candidate_created"] is False
    assert record["provenance_only"] is True
    assert record["queue_action"] == "none"


def test_headline_alone_never_matches_and_identifier_conflicts_are_diagnostic(tmp_path: Path):
    write_json(tmp_path / "data/dispatches/ice/incidents/incident.json", {
        "incident_id": "incident-123",
        "title": "Historical ICE incident",
        "event_date": "2026-06-30",
        "event_category": "enforcement_operation",
        "location_name": "Phoenix",
    })
    unmatched, _ = normalize(tmp_path, finding(
        finding_id="headline-only",
        source_url="https://www.ice.gov/news/releases/different",
        canonical_source_url="https://www.ice.gov/news/releases/different",
    ))
    assert unmatched["historical_outcome"] == "new_historical_candidate"
    conflicted, _ = normalize(tmp_path, finding(incident_id="incident-123"))
    assert conflicted["historical_outcome"] == "needs_manual_review"
    assert conflicted["match_basis"] == "incident_identifier_with_conflicts"
    assert {item["field"] for item in conflicted["conflicting_fields"]} >= {"event_date", "location_name"}


def test_unrelated_universal_event_is_not_an_ice_match_target(tmp_path: Path):
    write_json(tmp_path / "data/universal_events/healthcare/event.json", {
        "domain": "healthcare_access",
        "event_id": "event-healthcare-1",
        "source_url": "https://agency.gov/shared-record",
        "event_date": "2026-07-01",
        "event_category": "service_reduction",
    })
    record, _ = normalize(tmp_path, finding(
        source_url="https://agency.gov/shared-record",
        canonical_source_url="https://agency.gov/shared-record",
    ))
    assert record["historical_outcome"] == "new_historical_candidate"
    assert record["provenance_links"] == []


def test_weak_evidence_is_archived_invalid(tmp_path: Path):
    record, outcomes = normalize(tmp_path, finding(exact_supporting_passage="General background only."))
    assert outcomes == {"archived_invalid": 1}
    assert record["review_status"] == "excluded"
    assert record["candidate_created"] is False
    assert record["publication_eligible"] is False


def test_context_only_policy_record_is_archived_context(tmp_path: Path):
    record, outcomes = normalize(tmp_path, finding(
        event_category="policy_change",
        context_only=True,
        severity="context",
        exact_supporting_passage="The report provides historical policy context and describes no current enforcement event.",
    ))
    assert outcomes == {"archived_context": 1}
    assert record["review_status"] == "historical_context"
    assert record["severity"] == "context"


def test_unsupported_elevated_severity_fails_closed(tmp_path: Path):
    record, outcomes = normalize(tmp_path, finding(severity="critical"))
    assert outcomes == {"archived_invalid": 1}
    assert record["historical_outcome"] == "archived_invalid"
    assert "critical severity" in record["exclusion_reason"]


def test_raw_bytes_and_historical_dates_are_preserved(tmp_path: Path, capsys):
    source = tmp_path / "ice-alert.json"
    raw = json.dumps(envelope(finding(event_date="2026-06-29", source_published_at="2026-07-02")), ensure_ascii=False).encode()
    source.write_bytes(raw)
    assert main(["import", "--domain", "ice", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    digest = hashlib.sha256(raw).hexdigest()
    archived = json.loads((tmp_path / f"data/agent-history/ice/raw/{digest}.json").read_text(encoding="utf-8"))
    normalized = json.loads((tmp_path / f"data/agent-history/ice/normalized/{digest}.json").read_text(encoding="utf-8"))
    assert result["status"] == "imported"
    assert base64.b64decode(archived["raw_bytes_base64"]) == raw
    assert normalized["findings"][0]["event_date"] == "2026-06-29"
    assert normalized["findings"][0]["source_published_at"] == "2026-07-02"
    assert archived["imported_at"] != normalized["findings"][0]["event_date"]


def test_ice_sidecar_normalizes_prose_and_hash_mismatch_fails_closed(tmp_path: Path, capsys):
    raw_path = tmp_path / "data/agent-history-staging/ice/alert.txt"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        "ICE reported one person was hospitalized in El Paso on July 1, 2026. "
        "The report was published July 2, 2026. "
        "https://www.ice.gov/news/releases/prose-incident\n",
        encoding="utf-8",
    )
    correction = sidecar(tmp_path, raw_path)
    before = raw_path.read_bytes()
    assert main(["dry-run", "--domain", "ice", "--input", str(raw_path), "--correction", str(correction), "--repo-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["normalization_method"] == "prose_envelope_to_structured_findings"
    assert result["ice_findings"][0]["event_category"] == "hospitalization"
    assert result["ice_findings"][0]["publication_eligible"] is False
    assert raw_path.read_bytes() == before
    value = json.loads(correction.read_text(encoding="utf-8"))
    value["raw_sha256"] = "0" * 64
    write_json(correction, value)
    with pytest.raises(ValueError, match="raw_sha256"):
        main(["dry-run", "--domain", "ice", "--input", str(raw_path), "--correction", str(correction), "--repo-root", str(tmp_path)])


def test_ice_sidecar_rejects_publication_approval_and_invented_location(tmp_path: Path):
    raw_path = tmp_path / "data/agent-history-staging/ice/alert.txt"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        "ICE reported one person was hospitalized in El Paso on July 1, 2026. "
        "The report was published July 2, 2026. "
        "https://www.ice.gov/news/releases/prose-incident\n",
        encoding="utf-8",
    )
    correction = sidecar(tmp_path, raw_path, overrides={"publication_approval": True})
    with pytest.raises(ValueError, match="cannot grant publication approval"):
        main(["dry-run", "--domain", "ice", "--input", str(raw_path), "--correction", str(correction), "--repo-root", str(tmp_path)])
    value = json.loads(correction.read_text(encoding="utf-8"))
    value["publication_approval"] = False
    value["findings"][0]["location_name"] = "Phoenix"
    write_json(correction, value)
    with pytest.raises(ValueError, match="location_name"):
        main(["dry-run", "--domain", "ice", "--input", str(raw_path), "--correction", str(correction), "--repo-root", str(tmp_path)])


def test_batch_validate_and_dry_run_write_nothing_and_report_private_metrics(tmp_path: Path, capsys):
    staging = tmp_path / "data/agent-history-staging/ice"
    write_json(staging / "alert.json", envelope(
        finding(finding_id="death", event_category="death_in_custody", fatalities=1, exact_supporting_passage="The report states that one person died in ICE custody."),
        finding(finding_id="legal", source_url="https://court.gov/order", canonical_source_url="https://court.gov/order", event_category="legal_ruling", legal_action=True, exact_supporting_passage="The court entered a documented legal ruling affecting detention operations."),
    ))
    for operation in ("batch-validate", "batch-dry-run"):
        code, result = run_json(capsys, [operation, "--domain", "ice", "--input-dir", str(staging), "--repo-root", str(tmp_path)])
        assert code == 0
        assert result["raw_runs"] == 1
        assert result["normalized_findings"] == 2
        assert result["critical_findings"] == 1
        assert result["fatalities"] == 1
        assert result["deaths_in_custody"] == 1
        assert result["legal_actions"] == 1
        assert result["publication_ready_count"] == 0
        assert not (tmp_path / "data/agent-history/ice").exists()


def test_repeat_batch_import_is_idempotent_and_updates_private_index(tmp_path: Path, capsys):
    staging = tmp_path / "data/agent-history-staging/ice"
    write_json(staging / "alert.json", envelope(finding()))
    args = ["batch-import", "--domain", "ice", "--input-dir", str(staging), "--repo-root", str(tmp_path)]
    code, first = run_json(capsys, args)
    assert code == 0
    code, second = run_json(capsys, args)
    assert code == 0
    assert first["imported_files"] == 1
    assert second["idempotent_files"] == 1
    index = json.loads((tmp_path / "data/agent-history/ice/reports/history-index.json").read_text(encoding="utf-8"))
    assert index["raw_run_count"] == 1
    assert index["normalized_finding_count"] == 1
    assert index["pending_review"] == 1
    assert index["publication_ready_count"] == 0


def test_private_import_does_not_mutate_public_or_cross_domain_state(tmp_path: Path, capsys):
    protected = [
        tmp_path / "output/site/marker.txt",
        tmp_path / "bluefern-dispatches-pages/marker.txt",
        tmp_path / "data/agent-history/food-line/marker.txt",
        tmp_path / "data/agent-history/care-line/marker.txt",
        tmp_path / "data/agent-history/gaza/marker.txt",
        tmp_path / "data/universal_events/publication-state/marker.txt",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged", encoding="utf-8")
    source = tmp_path / "ice-alert.json"
    write_json(source, envelope(finding()))
    assert main(["import", "--domain", "ice", "--input", str(source), "--repo-root", str(tmp_path)]) == 0
    capsys.readouterr()
    assert all(path.read_text(encoding="utf-8") == "unchanged" for path in protected)
    assert not (tmp_path / "data/dispatches/ice/queue").exists()
    assert not (tmp_path / "output/site/ice").exists()


def test_unicode_names_and_territorial_locations_remain_valid(tmp_path: Path):
    record, _ = normalize(tmp_path, finding(
        affected_population="Familia de José Álvarez",
        location_name="Mayagüez",
        city="Mayagüez",
        county=None,
        state_or_territory="PR",
        exact_supporting_passage="La fuente identifica a la familia de José Álvarez en Mayagüez, Puerto Rico.",
    ))
    assert record["affected_population"] == "Familia de José Álvarez"
    assert record["location_name"] == "Mayagüez"
    assert record["state_or_territory"] == "Puerto Rico"


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("PR", "Puerto Rico"),
        ("GU", "Guam"),
        ("USVI", "U.S. Virgin Islands"),
        ("CNMI", "Northern Mariana Islands"),
        ("AS", "American Samoa"),
    ],
)
def test_territories_normalize_to_distinct_canonical_names(tmp_path: Path, supplied: str, expected: str):
    record, _ = normalize(tmp_path, finding(state_or_territory=supplied))
    assert record["state_or_territory"] == expected


def test_district_of_columbia_is_distinct_from_states(tmp_path: Path):
    record, _ = normalize(tmp_path, finding(
        source_url="https://dc.gov/ice-response",
        canonical_source_url="https://dc.gov/ice-response",
        location_name="Washington",
        city="Washington",
        county=None,
        state_or_territory="D.C.",
        event_category="sanctuary_or_local_response",
        exact_supporting_passage="The District of Columbia issued a documented local response concerning immigration enforcement.",
    ))
    assert record["state_or_territory"] == "District of Columbia"
    assert record["state_or_territory"] not in {"Washington", "District of Columbia State"}


def test_detection_date_is_explicit_optional_and_distinct_from_other_dates(tmp_path: Path):
    record, _ = normalize(
        tmp_path,
        finding(
            event_date="2025-01 through 2026-07-28",
            source_published_at="2026-07-28",
            detection_date="2026-07-30",
        ),
    )
    assert record["event_date"] == "2025-01 through 2026-07-28"
    assert record["source_published_at"] == "2026-07-28"
    assert record["detection_date"] == "2026-07-30"
    missing, _ = normalize(tmp_path, finding(finding_id="missing-detection"))
    assert missing["detection_date"] is None
    timestamped, _ = normalize(
        tmp_path,
        finding(finding_id="timestamped", detection_date="2026-07-30T14:30:00Z"),
    )
    assert timestamped["detection_date"] == "2026-07-30T14:30:00Z"


def test_explicit_prose_and_markdown_detection_dates_normalize_without_ambient_inference(tmp_path: Path):
    markdown = "* **Detection Date:** July 30, 2026\n"
    prose, _ = normalize(tmp_path, finding(finding_id="markdown-date", raw_text=markdown))
    assert prose["detection_date"] == "2026-07-30"
    assert extract_detection_date(markdown) == "2026-07-30"
    assert normalize_detection_date(None) is None

    source = tmp_path / "2026-08-15-ice-alert.json"
    write_json(source, envelope(finding(finding_id="filename-only", detection_date=None)))
    source.touch()
    payload = json.loads(source.read_text(encoding="utf-8"))
    normalized, _ = normalize_records(
        tmp_path,
        "ice",
        payload,
        raw_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        captured_at="2026-09-01T12:00:00Z",
    )
    assert normalized[0]["detection_date"] is None


@pytest.mark.parametrize("invalid", ["2026-02-30", "2026-07-30T25:00:00Z", 20260730, ["2026-07-30"]])
def test_impossible_or_unsupported_detection_dates_fail_validation(
    tmp_path: Path,
    capsys,
    invalid: object,
):
    source = tmp_path / "ice-alert.json"
    write_json(source, envelope(finding(detection_date=invalid)))
    code, result = run_json(
        capsys,
        ["validate", "--domain", "ice", "--input", str(source), "--repo-root", str(tmp_path)],
    )
    assert code == 1
    assert result["valid"] is False
    assert result["invalid_detection_dates"] == [0]


def test_sidecar_detection_date_requires_matching_explicit_raw_field(tmp_path: Path, capsys):
    raw_path = tmp_path / "data/agent-history-staging/ice/alert.md"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        "* **Detection Date:** July 30, 2026\n\n"
        "ICE reported one person was hospitalized in El Paso on July 1, 2026. "
        "The report was published July 2, 2026. "
        "https://www.ice.gov/news/releases/prose-incident\n",
        encoding="utf-8",
    )
    correction = sidecar(tmp_path, raw_path)
    value = json.loads(correction.read_text(encoding="utf-8"))
    value["findings"][0]["detection_date"] = "2026-07-30"
    write_json(correction, value)
    code, result = run_json(
        capsys,
        [
            "dry-run",
            "--domain",
            "ice",
            "--input",
            str(raw_path),
            "--correction",
            str(correction),
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert code == 0
    assert result["ice_findings"][0]["detection_date"] == "2026-07-30"

    value["findings"][0]["detection_date"] = "2026-07-31"
    write_json(correction, value)
    with pytest.raises(ValueError, match="conflicts"):
        main(
            [
                "dry-run",
                "--domain",
                "ice",
                "--input",
                str(raw_path),
                "--correction",
                str(correction),
                "--repo-root",
                str(tmp_path),
            ]
        )
    value["findings"][0]["detection_date"] = "2026-07-30T12:00:00Z"
    write_json(correction, value)
    with pytest.raises(ValueError, match="conflicts"):
        main(
            [
                "dry-run",
                "--domain",
                "ice",
                "--input",
                str(raw_path),
                "--correction",
                str(correction),
                "--repo-root",
                str(tmp_path),
            ]
        )


def test_explicit_renormalization_updates_only_detection_date_and_is_idempotent(
    tmp_path: Path,
    capsys,
):
    protected = [
        tmp_path / "output/site/marker.txt",
        tmp_path / "bluefern-dispatches-pages/marker.txt",
        tmp_path / "data/agent-history/food-line/marker.txt",
        tmp_path / "data/agent-history/care-line/marker.txt",
        tmp_path / "data/agent-history/gaza/marker.txt",
        tmp_path / "data/universal_events/publication-state/marker.txt",
        tmp_path / "data/dispatches/ice/queue/marker.txt",
        tmp_path / "data/bluesky/marker.txt",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged", encoding="utf-8")

    raw_path = tmp_path / "data/agent-history-staging/ice/alert.md"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        "* **Detection Date:** July 30, 2026\n\n"
        "ICE reported one person was hospitalized in El Paso on July 1, 2026. "
        "The report was published July 2, 2026. "
        "https://www.ice.gov/news/releases/prose-incident\n",
        encoding="utf-8",
    )
    correction = sidecar(tmp_path, raw_path)
    import_args = [
        "import",
        "--domain",
        "ice",
        "--input",
        str(raw_path),
        "--correction",
        str(correction),
        "--repo-root",
        str(tmp_path),
    ]
    code, imported = run_json(capsys, import_args)
    assert code == 0
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    raw_archive = tmp_path / f"data/agent-history/ice/raw/{digest}.json"
    normalized_path = tmp_path / f"data/agent-history/ice/normalized/{digest}.json"
    report_path = tmp_path / f"data/agent-history/ice/reports/{digest}.json"

    legacy = json.loads(normalized_path.read_text(encoding="utf-8"))
    legacy["findings"][0].pop("detection_date", None)
    legacy["findings"][0].pop("last_normalized_at", None)
    legacy.pop("last_normalized_at", None)
    write_json(normalized_path, legacy)
    original_normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    original_raw_bytes = raw_archive.read_bytes()
    original_raw_digest = hashlib.sha256(original_raw_bytes).hexdigest()
    original_report_bytes = report_path.read_bytes()
    assert original_normalized["findings"][0]["historical_outcome"] == "new_historical_candidate"
    assert original_normalized["findings"][0]["candidate_created"] is True

    sidecar_value = json.loads(correction.read_text(encoding="utf-8"))
    sidecar_value["findings"][0]["detection_date"] = "2026-07-30"
    write_json(correction, sidecar_value)

    batch_args = [
        "batch-import",
        "--domain",
        "ice",
        "--input-dir",
        str(raw_path.parent),
        "--repo-root",
        str(tmp_path),
    ]
    code, ordinary = run_json(capsys, batch_args)
    assert code == 0
    assert ordinary["idempotent_files"] == 1
    assert json.loads(normalized_path.read_text(encoding="utf-8")) == original_normalized

    renormalize_args = [
        "renormalize",
        "--domain",
        "ice",
        "--input",
        str(raw_path),
        "--repo-root",
        str(tmp_path),
    ]
    code, revised = run_json(capsys, renormalize_args)
    assert code == 0
    assert revised["status"] == "renormalized"
    assert revised["changed_fields"] == [
        {
            "field": "detection_date",
            "finding_id": "ice-finding-1",
            "old_value": None,
            "new_value": "2026-07-30",
        }
    ]
    updated = json.loads(normalized_path.read_text(encoding="utf-8"))
    expected_finding = dict(original_normalized["findings"][0], detection_date="2026-07-30")
    assert updated["findings"][0] == expected_finding
    assert updated["last_normalized_at"]
    assert updated["findings"][0]["historical_outcome"] == "new_historical_candidate"
    assert updated["findings"][0]["candidate_created"] is True
    assert updated["findings"][0]["review_status"] == "pending_review"
    assert updated["findings"][0]["publication_eligible"] is False
    assert updated["findings"][0]["publication_approval"] is False
    assert raw_archive.read_bytes() == original_raw_bytes
    assert hashlib.sha256(raw_archive.read_bytes()).hexdigest() == original_raw_digest
    assert report_path.read_bytes() == original_report_bytes
    assert all(path.read_text(encoding="utf-8") == "unchanged" for path in protected)

    audit = json.loads(Path(revised["maintenance_audit_path"]).read_text(encoding="utf-8"))
    assert audit["raw_sha256"] == digest
    assert audit["changed_fields"] == revised["changed_fields"]
    assert audit["source_evidence"]["raw_value"] == "July 30, 2026"
    assert audit["publication_approval"] is False
    assert revised["inventory_before"]["raw_run_count"] == 1
    assert revised["inventory_after"]["raw_run_count"] == 1
    assert revised["inventory_after"]["normalized_finding_count"] == 1
    assert revised["inventory_after"]["historical_candidate_count"] == 1
    assert revised["inventory_after"]["pending_review"] == 1
    assert revised["inventory_after"]["publication_ready_count"] == 0

    revised_digest = hashlib.sha256(normalized_path.read_bytes()).hexdigest()
    code, repeated = run_json(capsys, renormalize_args)
    assert code == 0
    assert repeated["status"] == "idempotent_noop"
    assert hashlib.sha256(normalized_path.read_bytes()).hexdigest() == revised_digest
    assert repeated["inventory"]["raw_run_count"] == 1
    assert repeated["inventory"]["normalized_finding_count"] == 1
    assert repeated["inventory"]["pending_review"] == 1
    assert repeated["inventory"]["publication_ready_count"] == 0
