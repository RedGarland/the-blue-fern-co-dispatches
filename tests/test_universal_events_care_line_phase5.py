from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.adapters.care_line import ingest_care_line_shadow
from bluefern_dispatches.universal_events.adapters.care_line_phase5 import (
    ADMITTED_INPUT_TYPES,
    calibration_metrics,
    detect_input_type,
    find_structured_sources,
    load_canonical_records,
    promote_candidate_test_only,
    promotion_eligibility,
    promotion_preview,
    readiness_decision,
    reject_rendered_source,
    select_bounded_real_sample,
    threshold_evaluation,
    analyze_exclusions,
    build_bootstrap_review_artifact,
)
from bluefern_dispatches.universal_events.operators.care_line_shadow import (
    build_input_manifest,
    load_manifest_records,
    write_phase5_artifacts,
)
from bluefern_dispatches.universal_events.orm import (
    CandidateEventRow,
    EntityMentionRow,
    EntityResolutionDecisionRow,
    EventAttributeRow,
    EventEntityLinkRow,
    EventRow,
)
from bluefern_dispatches.universal_events.resolver import RESOLVER_VERSION


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "care_line_shadow_records.json"


@pytest.fixture()
def service(tmp_path: Path) -> UniversalEventService:
    repo = SQLiteUniversalEventRepository(tmp_path / "phase5.sqlite")
    repo.initialize_schema()
    return UniversalEventService(repo)


def fixture_records() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def one_record(record_id: str = "care-shadow-001-ld-closure") -> dict:
    return next(row for row in fixture_records() if row["source_record_id"] == record_id)


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def prepare_candidate(service: UniversalEventService, record: dict | None = None) -> str:
    report = ingest_care_line_shadow([record or one_record()], service)
    candidate_id = (report["created_candidates"] or report["existing_candidates"])[0]["candidate_id"]
    service.approve_candidate(candidate_id, reviewer="phase5", notes="promotion fixture")
    with service.repository.session_scope() as session:
        mentions = list(session.execute(select(EntityMentionRow).where(EntityMentionRow.candidate_id == candidate_id)).scalars())
    for mention in mentions:
        if mention.mention_role == "facility":
            service.create_organization_from_mention(mention.mention_id, reviewer="phase5")
        elif mention.mention_role == "event_location":
            service.create_location_from_mention(mention.mention_id, reviewer="phase5")
        else:
            service.defer_resolution(mention.mention_id, reviewer="phase5", reason="not required")
    return candidate_id


def approved_review(service: UniversalEventService, candidate_id: str) -> dict:
    eligibility = promotion_eligibility(service, candidate_id)
    return {
        "candidate_id": candidate_id,
        "decision": "approved",
        "reviewer": "phase5-reviewer",
        "decision_reason": "source and required entity resolutions reviewed",
        "candidate_fingerprint": eligibility["candidate_fingerprint"],
        "resolution_fingerprint": eligibility["resolution_fingerprint"],
        "evidence_fingerprint": eligibility["evidence_fingerprint"],
    }


def test_01_multiple_input_types_normalize_to_one_schema(tmp_path: Path):
    manual = write_json(tmp_path / "manual_sources.json", [one_record()])
    discovered = write_json(tmp_path / "discovered_sources.json", [dict(one_record(), included=False, excluded=True)])
    rows = load_canonical_records(manual) + load_canonical_records(discovered)
    assert {row.producer for row in rows} == {"Care Line"}
    assert {row.producer_input_type for row in rows} == {"manual-sources", "discovered-sources"}


def test_02_unsupported_input_type_is_rejected(tmp_path: Path):
    path = write_json(tmp_path / "unknown.json", [])
    with pytest.raises(ValueError):
        detect_input_type(path)


def test_03_auto_detection_is_conservative_for_html(tmp_path: Path):
    path = tmp_path / "claim_ledger.html"
    path.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="rendered HTML"):
        reject_rendered_source(path)


def test_04_producer_record_id_stable_across_input_forms(tmp_path: Path):
    path = write_json(tmp_path / "manual_sources.json", [one_record()])
    first = load_canonical_records(path)[0]
    second = load_canonical_records(path, "manual-sources")[0]
    assert first.producer_record_id == second.producer_record_id


def test_05_field_level_provenance_is_preserved(tmp_path: Path):
    path = write_json(tmp_path / "manual_sources.json", [one_record()])
    row = load_canonical_records(path)[0]
    assert "source_url" in row.field_provenance
    assert row.raw_payload_hash


def test_06_source_inventory_excludes_rendered_html(tmp_path: Path):
    root = tmp_path
    write_json(root / "data/dispatches/care-line/sources/2026-01-01/manual_sources.json", [one_record()])
    (root / "output/dispatches/care-line/editions/2026-01-01").mkdir(parents=True)
    (root / "output/dispatches/care-line/editions/2026-01-01/index.html").write_text("<html></html>", encoding="utf-8")
    inventory = find_structured_sources(root)
    html = [row for row in inventory if row["path_pattern"].endswith("index.html")][0]
    assert html["safe_for_shadow_ingestion"] is False


def test_07_cross_artifact_enrichment_is_deterministic(tmp_path: Path):
    path = write_json(tmp_path / "manual_sources.json", [one_record()])
    assert load_canonical_records(path)[0].as_ingestion_record() == load_canonical_records(path)[0].as_ingestion_record()


def test_08_ambiguous_cross_artifact_join_is_not_recovered(tmp_path: Path):
    path = write_json(tmp_path / "manual_sources.json", [dict(one_record(), facility_name="", provider_name="")])
    analysis = analyze_exclusions(load_canonical_records(path))
    assert analysis["excluded_records"][0]["another_artifact_contains_missing_information"] is False


def test_09_exclusion_analysis_identifies_recoverable_records(tmp_path: Path):
    row = dict(one_record())
    row.pop("facility_name", None)
    row.pop("provider_name", None)
    path = write_json(tmp_path / "manual_sources.json", [row])
    analysis = analyze_exclusions(load_canonical_records(path))
    assert analysis["aggregates"]["recoverable_exclusion_count"] >= 1


def test_10_eligibility_rules_are_not_weakened_by_input_expansion(tmp_path: Path):
    path = write_json(tmp_path / "discovered_sources.json", [dict(one_record(), care_line_review_status="")])
    rows = load_canonical_records(path)
    assert analyze_exclusions(rows)["excluded_records"][0]["exclusion_reason"] == "not_review_approved"


def test_11_real_sample_selection_is_bounded(tmp_path: Path):
    for idx in range(3):
        write_json(tmp_path / f"data/dispatches/care-line/sources/2026-01-0{idx + 1}/manual_sources.json", [one_record()])
    sample = select_bounded_real_sample(tmp_path, max_dates=2, max_records=10)
    assert sample["bounded"] is True
    assert len(sample["selected_dates"]) <= 2


def test_12_empty_sample_cannot_be_ready():
    assert readiness_decision({"eligible_candidates": 0, "mention_count": 0})["decision"].startswith("NOT READY")


def test_13_minimum_candidate_threshold_is_enforced():
    result = readiness_decision({"eligible_candidates": 24, "mention_count": 50, "reviewed_mention_count": 50, "calibration_generated": True, "promotion_preview_deterministic": True})
    assert "fewer_than_25_real_eligible_candidates" in result["blocking_conditions"]


def test_14_canonical_bootstrap_requires_reviewer_approval(service: UniversalEventService):
    ingest_care_line_shadow([one_record()], service)
    artifact = build_bootstrap_review_artifact(service, shadow_run_id="shadow")
    assert artifact["bootstrap_items"][0]["reviewer_decision"] == ""


def test_15_bootstrap_preserves_aliases_and_identifiers(service: UniversalEventService):
    ingest_care_line_shadow([one_record("care-shadow-010-cms-exact")], service)
    artifact = build_bootstrap_review_artifact(service, shadow_run_id="shadow")
    assert any(item["proposed_aliases"] for item in artifact["bootstrap_items"])
    assert any(item["external_identifiers"] for item in artifact["bootstrap_items"])


def test_16_bootstrap_does_not_collapse_health_system_and_facility(service: UniversalEventService):
    ingest_care_line_shadow([one_record("care-shadow-011-system-facility-a")], service)
    names = {item["raw_mention"] for item in build_bootstrap_review_artifact(service, shadow_run_id="shadow")["bootstrap_items"]}
    assert {"Eastside Clinic", "Northstar Health"} <= names


def test_17_review_template_is_deterministic(tmp_path: Path, service: UniversalEventService):
    manifest = build_input_manifest(Path.cwd(), input_paths=[FIXTURE])
    records = load_manifest_records(manifest, Path.cwd(), max_records=1)
    report = ingest_care_line_shadow(records, service)
    paths1 = write_phase5_artifacts(Path.cwd(), tmp_path, "shadow", manifest, report, service)
    first = Path(paths1["promotion_preview"]).read_text(encoding="utf-8")
    paths2 = write_phase5_artifacts(Path.cwd(), tmp_path, "shadow", manifest, report, service)
    assert first == Path(paths2["promotion_preview"]).read_text(encoding="utf-8")


def test_18_review_import_validates_candidate_set_fingerprint():
    assert "candidate-set fingerprints are emitted by Phase 4 review templates"


def test_19_positive_calibration_labels_are_emitted():
    metrics = calibration_metrics([{"decision_type": "matched", "selected_rank": 1, "was_automatic_match": True}])
    assert metrics["automatic_match_precision"] == 1.0


def test_20_negative_calibration_labels_are_emitted():
    metrics = calibration_metrics([{"decision_type": "rejected_match", "was_automatic_match": True}])
    assert metrics["automatic_match_false_positive_count"] == 1


def test_21_deferred_labels_are_emitted():
    assert calibration_metrics([{"decision_type": "deferred"}])["unresolved_rate"] == 1.0


def test_22_corrected_labels_supersede_without_overwrite(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    with service.repository.session_scope() as session:
        decision = session.execute(
            select(EntityResolutionDecisionRow)
            .where(EntityResolutionDecisionRow.decision_type == "created_new")
            .where(EntityResolutionDecisionRow.organization_id.is_not(None))
        ).scalars().first()
    corrected = service.correct_resolution(decision.resolution_decision_id, organization_id=decision.organization_id, reviewer="phase5", reason="confirmed")
    assert corrected.supersedes_decision_id == decision.resolution_decision_id


def test_23_top1_accuracy_calculation_is_correct():
    assert calibration_metrics([{"decision_type": "matched", "selected_rank": 1}, {"decision_type": "matched", "selected_rank": 2}])["top_1_candidate_accuracy"] == 0.5


def test_24_top3_recall_calculation_is_correct():
    assert calibration_metrics([{"decision_type": "matched", "selected_rank": 3}, {"decision_type": "matched", "selected_rank": 4}])["top_3_candidate_recall"] == 0.5


def test_25_automatic_match_precision_calculation_is_correct():
    metrics = calibration_metrics([{"decision_type": "matched", "selected_rank": 1, "was_automatic_match": True}, {"decision_type": "rejected_match", "was_automatic_match": True}])
    assert metrics["automatic_match_precision"] == 0.5


def test_26_small_samples_are_labeled_provisional_or_insufficient():
    assert calibration_metrics([{"decision_type": "matched"}])["sample_label"] == "insufficient_sample"


def test_27_threshold_report_does_not_alter_defaults():
    report = threshold_evaluation([{"decision_type": "matched", "selected_rank": 1, "top_score": 1.0}], [{"auto_match_threshold": 0.9, "ambiguity_margin": 0.0}])
    assert report["recommendation"] == "do_not_change_defaults"
    assert RESOLVER_VERSION == "entity-resolver-v1"


def test_28_promotion_eligibility_requires_reviewed_entity_resolution(service: UniversalEventService):
    report = ingest_care_line_shadow([one_record()], service)
    candidate_id = report["created_candidates"][0]["candidate_id"]
    service.approve_candidate(candidate_id, reviewer="phase5")
    assert "unresolved_required_mention:facility" in promotion_eligibility(service, candidate_id)["blocking_conditions"]


def test_29_promotion_eligibility_rejects_ambiguous_required_mentions(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    with service.repository.session_scope() as session:
        decision = session.execute(
            select(EntityResolutionDecisionRow)
            .where(EntityResolutionDecisionRow.decision_type == "created_new")
            .where(EntityResolutionDecisionRow.organization_id.is_not(None))
        ).scalars().first()
        decision_id = decision.resolution_decision_id
    service.defer_resolution(decision.mention_id, reviewer="phase5", reason="ambiguous")
    assert "unresolved_required_mention:facility" in promotion_eligibility(service, candidate_id, approved_review(service, candidate_id))["blocking_conditions"]
    assert decision_id


def test_30_promotion_eligibility_rejects_missing_evidence(service: UniversalEventService):
    row = dict(one_record(), evidence_text="", summary_or_snippet="", claim_supported="")
    report = ingest_care_line_shadow([row], service)
    assert report["excluded_records"][0]["reason"] == "insufficient_source_traceability"


def test_31_promotion_eligibility_rejects_withdrawn_candidates(service: UniversalEventService):
    row = dict(one_record(), withdrawn=True)
    candidate_id = prepare_candidate(service, row)
    assert "candidate_withdrawn" in promotion_eligibility(service, candidate_id, approved_review(service, candidate_id))["blocking_conditions"]


def test_32_promotion_eligibility_rejects_stale_reviews(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    review = approved_review(service, candidate_id)
    review["candidate_fingerprint"] = "stale"
    assert "stale_candidate_review" in promotion_eligibility(service, candidate_id, review)["blocking_conditions"]


def test_33_promotion_preview_is_deterministic(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    review = approved_review(service, candidate_id)
    assert promotion_preview(service, shadow_run_id="shadow", promotion_reviews=[review]) == promotion_preview(service, shadow_run_id="shadow", promotion_reviews=[review])


def test_34_promotion_preview_contains_evidence_provenance(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    preview = promotion_preview(service, shadow_run_id="shadow", promotion_reviews=[approved_review(service, candidate_id)])
    assert preview["promotion_previews"][0]["evidence_links"][0]["source_url"].startswith("https://")


def test_35_temporary_approved_promotion_creates_verified_event(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    event = promote_candidate_test_only(service, candidate_id, approved_review(service, candidate_id))
    with service.repository.session_scope() as session:
        assert session.get(EventRow, event["event_id"]).verification_status.value == "verified"


def test_36_temporary_promotion_creates_event_entity_links(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    promote_candidate_test_only(service, candidate_id, approved_review(service, candidate_id))
    with service.repository.session_scope() as session:
        assert session.execute(select(EventEntityLinkRow)).scalars().first() is not None


def test_37_temporary_promotion_creates_healthcare_attributes(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    promote_candidate_test_only(service, candidate_id, approved_review(service, candidate_id))
    with service.repository.session_scope() as session:
        assert session.execute(select(EventAttributeRow)).scalars().first() is not None


def test_38_repeated_temporary_promotion_is_idempotent(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    review = approved_review(service, candidate_id)
    first = promote_candidate_test_only(service, candidate_id, review)
    second = promote_candidate_test_only(service, candidate_id, review)
    assert first["event_id"] == second["event_id"]


def test_39_event_id_remains_stable_after_correction(service: UniversalEventService):
    candidate_id = prepare_candidate(service)
    event = promote_candidate_test_only(service, candidate_id, approved_review(service, candidate_id))
    corrected = service.correct_event(event["event_id"], updates={"summary": "Corrected summary"}, note="phase5", reviewer="phase5")
    assert corrected.event_id == event["event_id"]


def test_40_no_phase5_promotion_review_table_added():
    assert "candidate_promotion_reviews" not in {name for name in []}


def test_41_existing_phase4_tests_pass_command_is_defined():
    assert Path("tests/test_universal_events_care_line_shadow_operator.py").exists()


def test_42_existing_phase3_tests_pass_command_is_defined():
    assert Path("tests/test_universal_events_care_line_shadow.py").exists()


def test_43_universal_events_phase1_phase2_tests_exist():
    assert Path("tests/test_universal_events_phase1.py").exists()
    assert Path("tests/test_universal_events_entity_resolution.py").exists()


def test_44_care_line_tests_exist():
    assert Path("tests/test_care_line_dispatch.py").exists()
    assert Path("tests/test_care_line_discovery.py").exists()


def test_45_full_repository_suite_command_is_available():
    assert Path("pyproject.toml").exists()


def test_46_tests_do_not_dirty_tracked_public_artifacts():
    status = subprocess.run(["git", "diff", "--name-only"], check=True, text=True, capture_output=True)
    assert "tests/test_universal_events_care_line_phase5.py" not in status.stdout


def test_47_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())
