from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from bluefern_dispatches.food_line_current_review import load_queue
from bluefern_dispatches.source_based_qualification import assess_review_retention
from scripts.process_food_line_current_intake import _build_review_queue


EDITION = "2026-09-05"


def _source(evidence: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "canonical_source_url": "https://example.org/source",
        "publisher": "Example Operator",
        "exact_supporting_passage": evidence,
        "source_published_at": EDITION,
        "discovered_at": f"{EDITION}T12:00:00Z",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("case", "record", "expected_basis"),
    [
        (
            "undated first-party upcoming closure",
            _source(
                "The Pitcairn Community Pantry will close permanently on September 20, 2026.",
                source_published_at="",
                source_role="first_party_provider",
            ),
            "newly_effective",
        ),
        (
            "older report effective now",
            _source(
                "The town's only grocery store will close effective September 5, 2026, leaving Latta without a supermarket.",
                source_published_at="2026-08-12",
            ),
            "newly_effective",
        ),
        (
            "same-day unmet demand",
            _source("The provider is unable to supply food to everyone seeking assistance in North Richmond."),
            "newly_published",
        ),
        (
            "disaster food loss",
            _source("The state documented households that lost food during the storm and opened SNAP replacement benefits."),
            "newly_published",
        ),
        (
            "fundraising frame with shortage",
            _source("A donation drive began after the Salvation Army pantry reported inventory shortages and running low on staples."),
            "newly_published",
        ),
        (
            "completed recurring distribution closure",
            _source("Harvesters permanently ended its monthly food distribution in Topeka on September 5, 2026."),
            "newly_published",
        ),
        (
            "one of multiple access points lost",
            _source("One of the university's two campus pantries closed permanently, while the main pantry remains open."),
            "newly_published",
        ),
        (
            "temporary satellite closure",
            _source("The satellite pantry closed temporarily with no reopening date; another location remains available."),
            "newly_published",
        ),
        (
            "replacement denial after disaster loss",
            _source("After households lost food in the outage, their replacement SNAP claims were denied."),
            "newly_published",
        ),
        (
            "supply contraction without turnaways",
            _source("The food bank reported a supply contraction and procurement costs that threaten service capacity."),
            "newly_published",
        ),
        (
            "geographic service gap",
            _source("Western Slope communities have an emergency food geographic gap with no nearby pantry."),
            "newly_published",
        ),
    ],
)
def test_food_line_failure_classes_are_retained_for_review(case: str, record: dict[str, object], expected_basis: str) -> None:
    result = assess_review_retention(record, dispatch="food-line", edition_date=EDITION)

    assert result["eligible_for_review"] is True, case
    assert result["disposition"] == "retained_for_review"
    assert result["freshness_basis"] == expected_basis
    assert result["next_transition_owner"] == "human_editorial_review"


@pytest.mark.parametrize(
    ("case", "evidence"),
    [
        ("facility closure", "Mercy Hospital will close permanently on September 20, 2026, ending local emergency access."),
        ("partial location reduction", "One of two satellite clinics closed, requiring patients to use the remaining site."),
        ("temporary suspension", "The dialysis service is suspended indefinitely with no reopening date."),
        ("capacity shortage", "A staffing shortage reduced clinic capacity and created an appointment backlog."),
        ("geographic access loss", "The rural clinic closure creates a healthcare geographic gap with no nearby provider."),
        ("demand capacity mismatch", "The emergency department is unable to meet patient demand and is over capacity."),
    ],
)
def test_care_line_analogs_use_the_same_review_retention_contract(case: str, evidence: str) -> None:
    result = assess_review_retention(_source(evidence), dispatch="care-line", edition_date=EDITION)

    assert result["eligible_for_review"] is True, case
    assert result["disposition"] == "retained_for_review"
    assert result["traceable_source"] is True


@pytest.mark.parametrize(
    ("case", "record", "reason"),
    [
        ("generic fundraiser", _source("Please donate to support our annual fundraising campaign."), "no_explicit_concrete_strain_evidence"),
        ("routine closure", _source("The pantry has routine scheduled holiday hours and will close for Labor Day."), "routine_scheduled_or_seasonal_gap_without_demonstrated_pressure"),
        ("academic gap", _source("The campus pantry follows the academic calendar and closes for summer break."), "routine_scheduled_or_seasonal_gap_without_demonstrated_pressure"),
        ("speculation", _source("The pantry might possibly close next year if funding changes."), "speculative_future_impact_without_documented_condition"),
        ("demand only", _source("The food bank reported rising demand this month."), "generic_rising_demand_without_access_or_capacity_consequence"),
        ("opinion only", _source("A columnist argues that food insecurity deserves more attention."), "no_explicit_concrete_strain_evidence"),
        ("explicitly negated closure", _source("The operator confirmed there is no current pantry closure or reduced schedule."), "no_explicit_concrete_strain_evidence"),
        ("untraceable", _source("The pantry closed permanently.", canonical_source_url=""), "invalid_or_missing_https_url"),
    ],
)
def test_negative_cases_remain_fail_closed(case: str, record: dict[str, object], reason: str) -> None:
    result = assess_review_retention(record, dispatch="food-line", edition_date=EDITION)

    assert result["eligible_for_review"] is False, case
    assert reason in result["failure_reasons"]
    assert result["disposition"] in {"rejected_with_reason", "invalid_source_with_reason"}


def test_source_watch_shape_survives_normalization_without_inventing_publication_date() -> None:
    payload = {
        "final_trace_url": "https://example.org/pantry",
        "discovered_title": "Pantry suspension",
        "discovered_publisher": "Example Food Bank",
        "evidence_text": "The weekly pantry remains suspended indefinitely and had served 250 families.",
        "pressure_summary": "A weekly distribution remains suspended.",
        "source_published_date": "",
        "retrieved_at": f"{EDITION}T12:00:00Z",
        "source_family": "food_bank_provider",
        "source_role": "provider_signal",
        "location_name": "Palm Springs",
        "state_abbrev": "CA",
    }
    finding = adapt_food_line_agent_output([payload], agent_name="Food Line Source Watch", agent_run_id="run-1")[0]
    candidate = map_finding_to_food_line_candidate(finding, edition_date=EDITION)

    assert finding.title == "Pantry suspension"
    assert finding.publisher == "Example Food Bank"
    assert finding.canonical_source_url == "https://example.org/pantry"
    assert finding.exact_supporting_passage.startswith("The weekly pantry")
    assert finding.source_published_at == ""
    assert candidate["eligible_for_review"] is True
    assert candidate["freshness_check"]["basis"] == "current_first_party_status"


def test_missing_secondary_details_constrain_wording_without_erasing_the_signal() -> None:
    result = assess_review_retention(
        _source(
            "The pantry reported an inventory shortage and cannot supply food to everyone seeking assistance.",
            uncertainty_note="No exact inventory count or confirmed turnaway total is available.",
        ),
        dispatch="food-line",
        edition_date=EDITION,
    )

    assert result["eligible_for_review"] is True
    assert "No exact inventory count" in result["uncertainty_note"]
    assert "limited to the traceable supporting evidence" in result["uncertainty_note"]


def test_every_discovery_has_a_durable_disposition_and_duplicates_do_not_enter_review(tmp_path: Path) -> None:
    inbox = tmp_path / "data/dispatches/food-line/agent-inbox"
    inbox.mkdir(parents=True)
    good = {
        "final_trace_url": "https://example.org/pantry",
        "discovered_title": "Pantry closes",
        "discovered_publisher": "Example News",
        "evidence_text": "The pantry closed permanently on September 5, 2026.",
        "source_published_date": EDITION,
        "location_name": "Example City",
        "state_abbrev": "CA",
    }
    invalid = {**good, "final_trace_url": "", "canonical_url": "", "source_url": "", "discovered_title": "Untraceable claim"}
    payload = {"agent_name": "Food Line Source Watch", "agent_run_id": "run-1", "findings": [good, dict(good), invalid]}
    (inbox / "run.json").write_text(json.dumps(payload), encoding="utf-8")

    queue = _build_review_queue(tmp_path, EDITION, inbox)
    validated = load_queue(tmp_path / "data/dispatches/food-line/review/current-signal-review.json")
    intake = json.loads((tmp_path / "data/dispatches/food-line/agent-intake/2026-09-05/run-1.json").read_text(encoding="utf-8"))

    assert queue == validated
    assert len(queue["items"]) == 1
    assert [row["candidate_disposition"] for row in intake["candidate_rows"]] == [
        "retained_for_review",
        "duplicate",
        "invalid_source_with_reason",
    ]
    assert intake["lifecycle_reconciliation"] == {"discovered": 3, "terminal_or_handoff": 3, "unaccounted": 0}
    assert queue["review_lifecycle"]["unaccounted"] == 0
    assert queue["review_lifecycle"]["pending_review_owner"] == "human_editorial_review"


def test_multiple_source_watch_exports_receive_unique_review_ranks(tmp_path: Path) -> None:
    inbox = tmp_path / "data/dispatches/food-line/agent-inbox"
    inbox.mkdir(parents=True)
    for index in (1, 2):
        payload = {
            "agent_name": "Food Line Source Watch",
            "agent_run_id": f"run-{index}",
            "findings": [
                {
                    "final_trace_url": f"https://example.org/pantry-{index}",
                    "discovered_title": f"Pantry {index} closes",
                    "discovered_publisher": "Example News",
                    "evidence_text": f"Pantry {index} closed permanently on September 5, 2026.",
                    "source_published_date": EDITION,
                    "location_name": f"Example City {index}",
                    "state_abbrev": "CA",
                }
            ],
        }
        (inbox / f"run-{index}.json").write_text(json.dumps(payload), encoding="utf-8")

    queue = _build_review_queue(tmp_path, EDITION, inbox)

    assert len(queue["items"]) == 2
    assert len({item["proposed_rank"] for item in queue["items"]}) == 2
    assert queue["review_lifecycle"]["unaccounted"] == 0
