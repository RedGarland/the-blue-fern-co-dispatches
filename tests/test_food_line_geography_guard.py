from __future__ import annotations

from bluefern_dispatches.adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from bluefern_dispatches.food_line_discovery_bridge import _bridge_record_from_candidate
from bluefern_dispatches.food_line_discovery_expansion import _apply_public_readiness_gate
from bluefern_dispatches.food_line_sources import food_line_supported_geography_result
from bluefern_dispatches.source_based_qualification import assess_review_retention
from scripts.process_food_line_current_intake import _queue_item_from_candidate
from scripts.run_food_line_dispatch import _food_line_public_inclusion_reason


def _finding_payload(**overrides):
    row = {
        "title": "Pantry demand rises",
        "publisher": "Foreign Publisher",
        "source_url": "https://example.com/pantry-demand-rises",
        "source_published_at": "2026-10-17",
        "exact_supporting_passage": "The pantry cannot keep up with rising demand for food assistance.",
        "summary": "A current food-access pressure signal.",
        "state": "NC",
        "location_name": "Raleigh",
        "geographic_scope": "national",
    }
    row.update(overrides)
    return row


def test_missing_event_geography_fails_closed_and_keeps_query_hints_private():
    result = food_line_supported_geography_result({"query_state_hint": "NC", "query_location_hint": "Raleigh"})
    assert result["status"] == "unknown"
    finding = adapt_food_line_agent_output([_finding_payload()], agent_name="fixture", agent_run_id="r1")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    assert candidate["state"] == ""
    assert candidate["location_name"] == ""
    assert candidate["query_state_hint"] == "NC"
    assert candidate["eligible_for_review"] is False
    assert "food_line_event_geography_unverified" in candidate["exclusion_reason"]


def test_exact_nl_times_retained_excerpt_cannot_bypass_on_causes_or_bare_us():
    payload = _finding_payload(
        title="Rising demand for aid causes poverty relief groups to start putting people on a waitlist",
        publisher="NL Times",
        source_url="https://nltimes.nl/2026/10/17/rising-demand-aid-causes-poverty-relief-groups-start-putting-people-waitlist",
        state="",
        location_name="",
        geographic_scope="national",
        exact_supporting_passage=(
            "Stichting Armoedefonds research among more than 650 local organizations "
            "found rising requests, with 11% operating a waiting list and pressure "
            "from groceries, energy, and school-supply costs."
        ),
    )
    finding = adapt_food_line_agent_output(
        [payload], agent_name="Food Line Source Watch", agent_run_id="nl-times-regression"
    )[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    assert candidate["food_line_geography_status"] == "unknown"
    assert candidate["supported_product_geography"] is False
    assert candidate["eligible_for_review"] is False
    assert "food_line_event_geography_unverified" in candidate["exclusion_reason"]
    assert food_line_supported_geography_result(
        {"evidence_text": "causes us concern", "geography_evidence_basis": "source_body"}
    )["status"] == "unknown"


def test_exact_nl_times_candidate_reaches_queue_without_public_geography():
    payload = _finding_payload(
        title="Rising demand for aid causes poverty relief groups to start putting people on a waitlist",
        publisher="NL Times",
        source_url="https://nltimes.nl/2026/10/17/rising-demand-aid-causes-poverty-relief-groups-start-putting-people-waitlist",
        state="",
        location_name="",
        geographic_scope="national",
        exact_supporting_passage=(
            "Stichting Armoedefonds research among more than 650 local organizations found rising requests, "
            "with 11% operating a waiting list and pressure from groceries, energy, and school-supply costs."
        ),
    )
    finding = adapt_food_line_agent_output([payload], agent_name="Food Line Source Watch", agent_run_id="nl-queue-regression")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    queued = _queue_item_from_candidate(
        candidate,
        edition_date="2026-10-17",
        source_artifact_path="data/dispatches/food-line/agent-intake/2026-10-17/nl.json",
        proposed_rank=1,
    )
    assert candidate["raw_agent_payload"]["publisher"] == "NL Times"
    assert candidate["exact_supporting_passage"] == payload["exact_supporting_passage"]
    assert queued["state"] == ""
    assert queued["location_name"] == ""
    assert queued["eligible_for_review"] is False
    assert queued["review_retention_disposition"] == "rejected_with_reason"


def test_explicit_foreign_event_is_outside_even_with_us_query_state():
    result = food_line_supported_geography_result(
        {"event_country": "Netherlands", "event_state": "US", "state": "NC"}
    )
    assert result["status"] == "outside"
    assert not result["supported"]


def test_explicit_us_event_from_foreign_publisher_is_supported():
    result = food_line_supported_geography_result(
        {"publisher_country": "Canada", "event_country": "United States", "event_state": "MA"}
    )
    assert result["status"] == "supported"
    assert result["supported"]


def test_source_body_can_establish_event_geography_without_publisher_or_query_inference():
    outside = food_line_supported_geography_result(
        {"evidence_text": "The Netherlands food bank reported rising demand.", "geography_evidence_basis": "source_body"}
    )
    domestic = food_line_supported_geography_result(
        {"evidence_text": "Arizona food banks reported rising demand.", "geography_evidence_basis": "source_body"}
    )
    assert outside["status"] == "outside"
    assert domestic["status"] == "supported"
    assert domestic["normalized_state"] == "AZ"
    assert domestic["normalized_location_name"] == "Arizona"
    assert domestic["normalized_location_scope"] == "state_local"


def test_composite_discovery_text_without_geography_basis_stays_private():
    result = food_line_supported_geography_result(
        {
            "title": "New York Times food pressure",
            "publisher": "NewYorkTimes.com",
            "url": "https://www.nytimes.com/2026/10/17/food-pressure.html",
            "query_text": "newyork food pantry us",
            "evidence_text": "New York Times | food pressure | https://www.nytimes.com/2026/10/17/food-pressure.html | newyork us",
        }
    )
    assert result["status"] == "unknown"
    assert not result["supported"]


def test_adapter_does_not_promote_composite_evidence_into_exact_passage():
    payload = _finding_payload(
        state="",
        location_name="",
        event_country="",
        event_state="",
        event_location="",
        exact_supporting_passage="",
        evidence_text="New York Times | https://www.nytimes.com/story | newyork query | us",
        evidence_text_basis="page_text_excerpt",
    )
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="composite-private")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    assert finding.exact_supporting_passage == ""
    assert candidate["evidence_text"] == ""
    assert candidate["eligible_for_review"] is False
    assert candidate["supported_product_geography"] is False


def test_source_body_arizona_flows_through_adapter_and_queue_at_state_precision():
    payload = _finding_payload(
        title="Arizona students lose automatic school-meal eligibility as SNAP cuts take effect",
        summary="School-age children receiving SNAP are losing automatic eligibility for free school meals in Arizona.",
        state="",
        location_name="",
        event_country="",
        event_state="",
        event_location="",
        exact_supporting_passage="Recent SNAP eligibility restrictions have caused thousands of Arizona students to lose automatic eligibility for free school meals.",
        pressure_type="benefit disruption",
        pressure_summary="SNAP eligibility restrictions reduced access to free school meals for Arizona students.",
        source_role="local_news",
        geography_evidence_basis="source_body",
    )
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="arizona-body")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    queued = _queue_item_from_candidate(
        candidate,
        edition_date="2026-10-17",
        source_artifact_path="data/dispatches/food-line/agent-intake/2026-10-17/arizona.json",
        proposed_rank=1,
    )
    assert candidate["state"] == "AZ"
    assert candidate["location_name"] == "Arizona"
    assert candidate["location_scope"] == "state_local"
    assert queued["state"] == "AZ"
    assert queued["location_name"] == "Arizona"
    assert queued["location_scope"] == "state_local"
    assert queued["supported_product_geography"] is True
    assert queued["food_line_geography_status"] == "supported"
    assert "food_line_event_geography_unverified" not in queued["exclusion_reason"]


def test_british_and_unqualified_virgin_islands_do_not_become_us_scope():
    british = food_line_supported_geography_result(
        {"evidence_text": "The British Virgin Islands food system is under pressure.", "geography_evidence_basis": "source_body"}
    )
    unqualified = food_line_supported_geography_result(
        {"evidence_text": "The Virgin Islands food system is under pressure.", "geography_evidence_basis": "source_body"}
    )
    assert british["status"] == "outside"
    assert unqualified["status"] == "unknown"


def test_explicit_geography_fields_are_assessed_jointly():
    assert food_line_supported_geography_result({"event_country": "United States", "event_location": "Netherlands"})["status"] == "unknown_conflicting"
    assert food_line_supported_geography_result({"event_state": "NC", "event_location": "Netherlands"})["status"] == "unknown_conflicting"
    assert food_line_supported_geography_result({"country": "Netherlands", "event_country": "United States"})["status"] == "unknown_conflicting"


def test_country_conflict_survives_adapter_bridge_retention_queue_and_final_guard():
    payload = _finding_payload(
        country="Netherlands",
        event_country="United States",
        state="",
        event_state="",
        location_name="",
        exact_supporting_passage="Food banks in Arizona reported a documented supply shortage.",
        geography_evidence_basis="source_body",
        source_body_text="Food banks in Arizona reported a documented supply shortage.",
        pressure_type="supply contraction",
    )
    direct = food_line_supported_geography_result(payload)
    assert direct["status"] == "unknown_conflicting"
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="raw-conflict")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    assert candidate["country"] == "Netherlands"
    assert candidate["event_country"] == "United States"
    assert candidate["eligible_for_review"] is False
    assert "food_line_event_geography_unverified" in candidate["exclusion_reason"]

    retention = assess_review_retention(
        {**candidate, "canonical_source_url": candidate["canonical_url"]},
        dispatch="food-line",
        edition_date="2026-10-17",
    )
    assert retention["eligible_for_review"] is False
    assert "food_line_event_geography_unverified" in retention["failure_reasons"]

    bridge = _bridge_record_from_candidate(candidate)
    assert bridge["country"] == "Netherlands"
    assert bridge["event_country"] == "United States"
    assert bridge["food_line_geography_status"] == "unknown_conflicting"
    assert bridge["public_claim_eligible"] is False

    queued = _queue_item_from_candidate(
        candidate,
        edition_date="2026-10-17",
        source_artifact_path="data/dispatches/food-line/agent-intake/2026-10-17/raw-conflict.json",
        proposed_rank=1,
    )
    assert queued["country"] == "Netherlands"
    assert queued["event_country"] == "United States"
    assert queued["eligible_for_review"] is False
    assert queued["food_line_geography_status"] == "unknown_conflicting"
    assert _food_line_public_inclusion_reason(queued) == "event geography unverified"


def test_trusted_foreign_source_body_overrides_explicit_us_support_and_survives_handoffs():
    payload = _finding_payload(
        country="",
        event_country="United States",
        state="",
        event_state="",
        location_name="",
        event_location="",
        geography_evidence_basis="source_body",
        source_body_text="Food banks in the Netherlands reported a supply shortage",
        exact_supporting_passage="Food banks in the Netherlands reported a supply shortage",
        pressure_type="supply contraction",
    )
    direct = food_line_supported_geography_result(payload)
    assert direct["status"] == "unknown_conflicting"
    assert direct["reason"] == "source_text_conflicts_with_explicit_event_geography"
    assert direct["evidence_field"] == "source_body_text"

    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="body-us-conflict")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    assert candidate["event_country"] == "United States"
    assert candidate["source_body_text"] == payload["source_body_text"]
    assert candidate["food_line_geography_status"] == "unknown_conflicting"
    assert candidate["eligible_for_review"] is False

    retention = assess_review_retention(
        {**candidate, "canonical_source_url": candidate["canonical_url"]},
        dispatch="food-line",
        edition_date="2026-10-17",
    )
    assert retention["eligible_for_review"] is False
    assert "food_line_event_geography_unverified" in retention["failure_reasons"]

    bridge = _bridge_record_from_candidate(candidate)
    assert bridge["event_country"] == "United States"
    assert bridge["source_body_text"] == payload["source_body_text"]
    assert bridge["food_line_geography_status"] == "unknown_conflicting"
    assert bridge["public_claim_eligible"] is False

    queued = _queue_item_from_candidate(
        candidate,
        edition_date="2026-10-17",
        source_artifact_path="data/dispatches/food-line/agent-intake/2026-10-17/body-us-conflict.json",
        proposed_rank=1,
    )
    assert queued["event_country"] == "United States"
    assert queued["source_body_text"] == payload["source_body_text"]
    assert queued["food_line_geography_status"] == "unknown_conflicting"
    assert queued["eligible_for_review"] is False
    assert _food_line_public_inclusion_reason(queued) == "event geography unverified"


def test_state_location_conflict_survives_every_handoff_and_final_guard():
    payload = _finding_payload(
        country="",
        event_country="United States",
        state="",
        event_state="WV",
        location_name="",
        event_location="Virginia",
        exact_supporting_passage="Food banks reported a documented shortage in the affected event area.",
        geography_evidence_basis="explicit_event_fields",
    )
    direct = food_line_supported_geography_result(payload)
    assert direct["status"] == "unknown_conflicting"
    assert direct["reason"] == "conflicting_explicit_event_geography"

    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="state-location-conflict")[0]
    assert finding.state == "WV"
    assert finding.location_name == "Virginia"
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    assert candidate["state"] == "WV"
    assert candidate["event_state"] == "WV"
    assert candidate["location_name"] == "Virginia"
    assert candidate["event_location"] == "Virginia"
    assert candidate["food_line_geography_status"] == "unknown_conflicting"
    assert candidate["eligible_for_review"] is False

    retention = assess_review_retention(
        {**candidate, "canonical_source_url": candidate["canonical_url"]},
        dispatch="food-line",
        edition_date="2026-10-17",
    )
    assert retention["eligible_for_review"] is False
    assert "food_line_event_geography_unverified" in retention["failure_reasons"]

    bridge = _bridge_record_from_candidate(candidate)
    assert bridge["event_state"] == "WV"
    assert bridge["event_location"] == "Virginia"
    assert bridge["food_line_geography_status"] == "unknown_conflicting"
    assert bridge["public_claim_eligible"] is False

    queued = _queue_item_from_candidate(
        candidate,
        edition_date="2026-10-17",
        source_artifact_path="data/dispatches/food-line/agent-intake/2026-10-17/state-location-conflict.json",
        proposed_rank=1,
    )
    assert queued["event_state"] == "WV"
    assert queued["event_location"] == "Virginia"
    assert queued["eligible_for_review"] is False
    assert queued["food_line_geography_status"] == "unknown_conflicting"
    assert _food_line_public_inclusion_reason(queued) == "event geography unverified"


def test_state_alias_matching_prefers_specific_phrases_and_fails_closed_on_conflicts():
    west_virginia = food_line_supported_geography_result(
        {"evidence_text": "West Virginia food banks reported a documented shortage.", "geography_evidence_basis": "source_body"}
    )
    assert west_virginia["status"] == "supported"
    assert west_virginia["normalized_state"] == "WV"

    multiple_body_states = food_line_supported_geography_result(
        {
            "evidence_text": "Arizona and North Carolina food banks reported shortages.",
            "geography_evidence_basis": "source_body",
        }
    )
    assert multiple_body_states["status"] == "unknown_conflicting"
    assert multiple_body_states["reason"] == "multiple_source_text_us_states"

    explicit_conflict = food_line_supported_geography_result(
        {"event_state": "West Virginia", "state": "Virginia"}
    )
    assert explicit_conflict["status"] == "unknown_conflicting"
    assert explicit_conflict["reason"] == "conflicting_explicit_event_states"


def test_source_body_matcher_uses_provenance_without_promoting_common_words():
    for passage in (
        "Rising demand causes households to seek help.",
        "The suspended service left households waiting.",
        "The report says the policy causes us concern.",
    ):
        result = food_line_supported_geography_result(
            {"evidence_text": passage, "geography_evidence_basis": "source_body"}
        )
        assert result["status"] == "unknown", passage

    for passage, state in (
        ("Arizona food banks reported rising demand.", "AZ"),
        ("North Carolina pantry operators reported a shortage.", "NC"),
    ):
        result = food_line_supported_geography_result(
            {"evidence_text": passage, "geography_evidence_basis": "source_body"}
        )
        assert result["status"] == "supported"
        assert result["normalized_state"] == state

    assert food_line_supported_geography_result(
        {"evidence_text": "Food banks across the United States reported rising demand.", "geography_evidence_basis": "source_body"}
    )["supported"]
    assert food_line_supported_geography_result(
        {"evidence_text": "Food assistance providers in Puerto Rico reported shortages.", "geography_evidence_basis": "source_body"}
    )["normalized_state"] == "PR"
    assert food_line_supported_geography_result(
        {"evidence_text": "The Netherlands food bank reported rising demand.", "geography_evidence_basis": "source_body"}
    )["status"] == "outside"


def test_bare_uppercase_state_codes_in_source_prose_are_not_event_geography():
    for passage in ("The pantry has no food OR funding.", "HELP ME find a pantry.", "The report says AS needed."):
        result = food_line_supported_geography_result(
            {"evidence_text": passage, "geography_evidence_basis": "source_body"}
        )
        assert result["status"] == "unknown", passage
        assert result["supported"] is False


def test_agent_finding_keeps_legacy_state_and_location_hints_out_of_event_fields():
    payload = _finding_payload(event_state="", event_location="", state="NC", location_name="Raleigh")
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="hint-only")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-10-17")
    assert finding.state == ""
    assert finding.location_name == ""
    assert candidate["state"] == ""
    assert candidate["location_name"] == ""
    assert candidate["query_state_hint"] == "NC"
    assert candidate["query_location_hint"] == "Raleigh"
    assert candidate["eligible_for_review"] is False


def test_ambiguous_georgia_body_does_not_establish_us_state():
    result = food_line_supported_geography_result(
        {"evidence_text": "Georgia food banks reported rising demand.", "geography_evidence_basis": "source_body"}
    )
    assert result["status"] == "unknown"


def test_invalid_state_with_us_country_is_unknown_conflicting():
    result = food_line_supported_geography_result({"event_country": "United States", "event_state": "ZZ"})
    assert result["status"] == "unknown_conflicting"
    assert not result["supported"]


def test_us_territories_and_explicit_us_location_are_supported():
    for territory in ("PR", "GU", "VI", "AS", "MP"):
        result = food_line_supported_geography_result({"event_state": territory})
        assert result["supported"]
        assert result["normalized_state"] == territory
    assert food_line_supported_geography_result({"event_location": "United States"})["supported"]


def test_country_only_us_normalizes_to_national_queue_scope():
    row = _queue_item_from_candidate(
        {
            "candidate_id": "country-only",
            "title": "National pantry pressure",
            "publisher": "Publisher",
            "source_url": "https://example.com/national-pressure",
            "canonical_source_url": "https://example.com/national-pressure",
            "source_published_at": "2026-10-17",
            "exact_supporting_passage": "Food banks across the United States reported rising demand.",
            "event_country": "United States",
            "pressure_signal": True,
            "eligible_for_review": True,
        },
        edition_date="2026-10-17",
        source_artifact_path="data/dispatches/food-line/agent-intake/2026-10-17/country.json",
        proposed_rank=1,
    )
    assert row["eligible_for_review"] is True
    assert row["country"] == "United States"
    assert row["location_name"] == "United States"
    assert row["location_scope"] == "national"


def test_bridge_never_uses_query_scope_as_event_scope():
    row = _bridge_record_from_candidate(
        {
            "candidate_id": "candidate-1",
            "discovered_title": "Food pressure signal",
            "discovered_publisher": "NL Times",
            "discovered_url": "https://example.com/story",
            "geographic_scope": "national",
            "state_abbrev": "NC",
            "public_claim_eligible": True,
        }
    )
    assert row["state"] == ""
    assert row["location_name"] == ""
    assert row["query_state_hint"] == "NC"
    assert row["query_geographic_scope"] == "national"
    assert row["public_claim_eligible"] is False
    assert "food_line_event_geography_unverified" in row["public_claim_blockers"]


def test_public_readiness_gate_cannot_resurrect_prior_flags():
    row = {
        "discovered_title": "Food bank demand rises",
        "discovered_publisher": "Foreign Publisher",
        "source_url": "https://example.com/story",
        "source_published_date": "2026-10-17",
        "evidence_text": "The food bank reports rising demand and reduced capacity.",
        "pressure_signal": True,
        "pressure_type": "demand strain",
        "pressure_summary": "Demand is rising.",
        "affected_groups": ["families"],
        "evidence_level": "direct reporting",
        "freshness_role": "dated_recent_signal",
        "source_role": "local_news_report",
        "public_claim_eligible": True,
        "state_abbrev": "NC",
        "geographic_scope": "national",
    }
    _apply_public_readiness_gate(row, edition_date="2026-10-17")
    assert row["public_claim_eligible"] is False
    assert row["pressure_signal"] is True
    assert row["pressure_type"] == "demand strain"
    assert row["pressure_summary"] == "Demand is rising."
    assert "food_line_event_geography_unverified" in row["public_claim_blockers"]


def test_current_queue_item_does_not_default_to_us_and_rejects_unknown_geo():
    row = _queue_item_from_candidate(
        {
            "candidate_id": "candidate-queue-1",
            "title": "Pantry demand rises",
            "publisher": "Publisher",
            "source_url": "https://example.com/story",
            "canonical_source_url": "https://example.com/story",
            "source_published_at": "2026-10-17",
            "exact_supporting_passage": "The pantry cannot keep up with rising demand.",
            "summary_or_snippet": "Current pressure.",
            "pressure_signal": True,
            "eligible_for_review": True,
        },
        edition_date="2026-10-17",
        source_artifact_path="data/dispatches/food-line/agent-intake/2026-10-17/r.json",
        proposed_rank=1,
    )
    assert row["state"] == ""
    assert row["location_name"] == ""
    assert row["eligible_for_review"] is False
    assert row["review_retention_disposition"] == "rejected_with_reason"


def test_final_public_inclusion_rejects_unverified_geo_even_with_approval_flags():
    row = {
        "source_type": "approved_proposal",
        "source_public_story_eligible": True,
        "public_claim_eligible": True,
        "review_status": "approved",
        "pressure_signal": True,
        "supported_product_geography": True,
        "location_scope": "local",
    }
    assert _food_line_public_inclusion_reason(row) == "event geography unverified"


def test_care_retention_does_not_require_food_geography():
    decision = assess_review_retention(
        {
            "canonical_source_url": "https://example.com/care",
            "publisher": "Care Publisher",
            "exact_supporting_passage": "The clinic remains closed and access is unavailable.",
            "source_published_at": "2026-10-17",
            "event_type": "service closure",
            "access_consequences": ["closed"],
        },
        dispatch="care-line",
        edition_date="2026-10-17",
    )
    assert "food_line_event_geography_unverified" not in decision["failure_reasons"]
