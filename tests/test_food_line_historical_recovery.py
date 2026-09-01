from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_historical_recovery import (
    CONSEQUENCE_PRIORITIES,
    FOUR_TIER_PRIORITY_POLICY,
    FIVE_TIER_PRIORITY_POLICY,
    FoodLineHistoricalRecoveryError,
    _audit_four_tier_semantic_diff,
    _recovery_directory,
    _validated_current_recovery_identity,
    build_reconciliation,
    build_recovery,
    cluster_spec_template,
    import_recovery,
    migrate_recovery_to_four_tiers,
    parse_aggregate_handoff,
    sha256_bytes,
    validate_clusters,
)
from scripts.import_food_line_historical_recovery import main


def _finding(**overrides):
    row = {
        "title": "County pantry reduces distribution days",
        "publisher": "County News",
        "source_url": "https://news.example.org/pantry?utm_source=watch",
        "canonical_source_url": "https://news.example.org/pantry?utm_source=watch",
        "source_published_at": "2026-08-10",
        "exact_supporting_passage": "The pantry reduced distribution from five days to three days each week.",
        "summary": "The pantry reduced weekly service as demand increased.",
        "location_name": "Example County",
        "state": "AZ",
        "location_scope": "county",
        "affected_groups": ["pantry clients"],
        "pressure_type": "service_reduction",
        "confidence": "high",
        "source_role": "local_news",
        "evidence_level": "reported_direct_measurement",
        "agent_query_context": {"query": "food pantry demand"},
        "review_status": "pending_review",
        "exclusion_reason": None,
        "raw_agent_payload": {
            "uncertainty": "The source did not attribute the change to a single cause."
        },
    }
    row.update(overrides)
    return row


def _envelope(run_id: str, findings: list[dict]) -> dict:
    return {
        "schema_version": "food_line_agent_finding_v1",
        "agent_name": "source-watch",
        "agent_run_id": run_id,
        "started_at": "2026-08-11T01:00:00Z",
        "completed_at": "2026-08-11T01:05:00Z",
        "search_window": {"date_from": "2026-08-01", "date_to": "2026-08-10"},
        "findings": findings,
        "coverage_notes": "Private historical discovery.",
    }


def _aggregate(*envelopes: dict, malformed: bool = False) -> bytes:
    pieces = ["# Aggregate Food Line handoff\n"]
    for envelope in envelopes:
        pieces.append("```json\n" + json.dumps(envelope, ensure_ascii=False) + "\n```\n")
    if malformed:
        pieces.append('```json\n{"findings": [}\n```\n')
    return "".join(pieces).encode("utf-8")


def _cluster_spec(parsed: dict, *, disposition: str = "confirmed_historical_review_candidate") -> dict:
    finding_ids = [item["finding_id"] for item in parsed["findings"]]
    primary = finding_ids[0]
    return {
        "schema_version": "food_line_historical_event_cluster_spec_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": parsed["run_month"],
        "reviewed_by": "fixture-reviewer",
        "reviewed_at": "2026-09-01T00:00:00Z",
        "publication_approval": False,
        "unassigned_finding_ids": [],
        "clusters": [
            {
                "location": "Example County, Arizona",
                "organization": "Example County Pantry",
                "event_start_date": "2026-08-10",
                "event_end_date": "2026-08-10",
                "pressure_category": "service reduction",
                "underlying_development": "weekly food distribution reduced from five days to three",
                "affected_population": ["pantry clients"],
                "finding_ids": finding_ids,
                "primary_finding_id": primary,
                "measured_access_consequence": {
                    "type": "direct_service_loss_or_closure",
                    "description": "Two weekly distribution days were removed.",
                    "measurement": "five days to three days",
                    "supporting_finding_ids": [primary],
                },
                "uncertainty": {
                    "condition": {"status": "resolved", "note": "The service reduction was directly reported."},
                    "causal": {"status": "unresolved", "note": "The source did not allocate the change among causes."},
                    "severity": {"status": "unresolved", "note": "No turnaway count was reported."},
                },
                "prior_publication_match": {"status": "none"},
                "proposed_disposition": disposition,
                "disposition_reason": "A measured reduction in recurring food distribution is source-backed.",
                "unresolved_requirement": None,
                "exclusion_rule": None,
            }
        ],
    }


def _write_input_and_spec(tmp_path: Path, raw: bytes, spec: dict) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_path = tmp_path / "aggregate.md"
    input_path.write_bytes(raw)
    spec_path = tmp_path / "clusters.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return input_path, spec_path


def _import_five_tier_predecessor(
    root: Path,
    input_path: Path,
    spec_path: Path,
    *,
    captured_at: str = "2026-09-01T00:00:00Z",
    run_month: str | None = None,
) -> dict:
    artifacts = build_recovery(
        root,
        input_path,
        spec_path,
        pages_root=None,
        captured_at=captured_at,
        run_month=run_month,
    )
    for name, row_key in (
        ("event_cluster_manifest.json", "clusters"),
        ("priority_confirmed_candidates.json", "candidates"),
    ):
        for row in artifacts[name][row_key]:
            if row["measured_access_consequence"]["type"] == "disaster_household_food_loss":
                row["priority"] = 5
    return import_recovery(
        root,
        artifacts,
        cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()),
    )


def _migration_arguments(
    root: Path,
    input_path: Path,
    spec_path: Path,
    predecessor_artifact_set: str,
    *,
    source_commit: str = "a" * 40,
) -> dict:
    return {
        "root": root,
        "input_path": input_path,
        "cluster_spec_path": spec_path,
        "predecessor_artifact_set_sha256": predecessor_artifact_set,
        "implementation_source_commit": source_commit,
        "captured_at": "2026-09-01T00:00:00Z",
        "run_month": "2026-08",
    }


def _production_shaped_spec(parsed: dict) -> dict:
    finding_ids = [item["finding_id"] for item in parsed["findings"]]
    groups = []
    cursor = 0
    for index in range(414):
        size = 2 if index < 91 else 1
        groups.append(finding_ids[cursor : cursor + size])
        cursor += size
    assert cursor == 505

    consequence_types = (
        ["direct_service_loss_or_closure"] * 158
        + ["benefit_access_contraction_with_emergency_demand"] * 20
        + ["inventory_or_capacity_strain"] * 57
        + ["grocery_or_school_meal_access_loss"] * 64
        + ["disaster_household_food_loss"] * 21
        + ["disaster_household_food_loss"]
        + ["grocery_or_school_meal_access_loss"] * 93
    )
    dispositions = (
        ["confirmed_historical_review_candidate"] * 320
        + ["deferred_specific_evidence_gap"] * 67
        + ["excluded_under_existing_rules"] * 15
        + ["duplicate_or_corroboration"] * 10
        + ["already_published"] * 2
    )
    clusters = []
    for index, (group, consequence_type, disposition) in enumerate(
        zip(groups, consequence_types, dispositions, strict=True)
    ):
        prior_match = {"status": "none"}
        if disposition == "already_published":
            prior_match = {"status": "exact_source_url"}
        clusters.append(
            {
                "location": f"Synthetic County {index}, Arizona",
                "organization": f"Synthetic Food Operator {index}",
                "event_start_date": "2026-08-10",
                "event_end_date": "2026-08-10",
                "pressure_category": f"historical food pressure {index}",
                "underlying_development": f"bounded synthetic development {index}",
                "affected_population": ["households"],
                "finding_ids": group,
                "primary_finding_id": group[0],
                "measured_access_consequence": {
                    "type": consequence_type,
                    "description": f"Measured access consequence {index}.",
                    "measurement": str(index),
                    "supporting_finding_ids": [group[0]],
                },
                "uncertainty": {
                    "condition": {"status": "resolved", "note": "The condition is directly reported."},
                    "causal": {"status": "unresolved", "note": "Causal allocation remains unresolved."},
                    "severity": {"status": "unresolved", "note": "Full severity remains unresolved."},
                },
                "prior_publication_match": prior_match,
                "proposed_disposition": disposition,
                "disposition_reason": f"Event-specific reviewed disposition {index}.",
                "unresolved_requirement": (
                    f"Additional specific evidence {index} is required."
                    if disposition == "deferred_specific_evidence_gap"
                    else None
                ),
                "exclusion_rule": (
                    f"Existing bounded exclusion rule {index}."
                    if disposition == "excluded_under_existing_rules"
                    else None
                ),
            }
        )
    return {
        "schema_version": "food_line_historical_event_cluster_spec_v1",
        "input_sha256": parsed["input_sha256"],
        "run_month": "2026-08",
        "reviewed_by": "fixture-reviewer",
        "reviewed_at": "2026-09-01T00:00:00Z",
        "publication_approval": False,
        "unassigned_finding_ids": [],
        "clusters": clusters,
    }


def test_aggregate_parser_preserves_malformed_block_and_deduplicates_exact_findings() -> None:
    first = _finding()
    second = _finding(
        title="A separate pantry closes",
        source_url="https://news.example.org/pantry-closure?utm_campaign=x",
        canonical_source_url="https://news.example.org/pantry-closure?utm_campaign=x",
        exact_supporting_passage="The pantry closed permanently on August 12.",
        summary="A separate organization ended service.",
    )
    raw = _aggregate(_envelope("run-1", [first, second]), _envelope("run-1", [first]), malformed=True)

    parsed = parse_aggregate_handoff(raw)

    assert parsed["fence_count"] == 3
    assert parsed["valid_json_block_count"] == 2
    assert parsed["malformed_json_block_count"] == 1
    assert parsed["raw_finding_count"] == 3
    assert parsed["retained_finding_count"] == 2
    assert parsed["duplicate_finding_occurrence_count"] == 1
    assert parsed["unique_canonical_source_count"] == 2
    assert parsed["malformed_blocks"][0]["fence_index"] == 3
    urls = {item["canonical_source_url"] for item in parsed["sources"]}
    assert urls == {"https://news.example.org/pantry", "https://news.example.org/pantry-closure"}


def test_cluster_template_assigns_nothing_and_grants_no_authority() -> None:
    parsed = parse_aggregate_handoff(_aggregate(_envelope("run-1", [_finding()])))

    template = cluster_spec_template(parsed)

    assert template["publication_approval"] is False
    assert template["clusters"] == []
    assert template["unassigned_finding_ids"] == [parsed["findings"][0]["finding_id"]]


def test_run_month_scope_reports_but_does_not_retain_other_month_findings() -> None:
    august = _envelope("run-aug", [_finding()])
    july = _envelope("run-jul", [_finding(canonical_source_url="https://news.example.org/july", source_url="https://news.example.org/july")])
    july["started_at"] = "2026-07-31T01:00:00Z"
    july["completed_at"] = "2026-07-31T01:05:00Z"

    parsed = parse_aggregate_handoff(_aggregate(july, august), run_month="2026-08")

    assert parsed["raw_finding_count"] == 2
    assert parsed["retained_finding_count"] == 1
    assert parsed["out_of_scope_finding_count"] == 1
    assert parsed["unique_canonical_source_count"] == 1
    assert parsed["out_of_scope_findings"][0]["reason"] == "agent_run_outside_requested_month"


def test_causal_and_severity_uncertainty_do_not_block_measured_condition(tmp_path: Path) -> None:
    parsed = parse_aggregate_handoff(_aggregate(_envelope("run-1", [_finding()])))
    reconciliation = build_reconciliation(tmp_path, None, parsed["sources"])

    clusters = validate_clusters(
        _cluster_spec(parsed),
        parsed,
        reconciliation,
        root=tmp_path,
        pages_root=None,
    )

    assert clusters[0]["proposed_disposition"] == "confirmed_historical_review_candidate"
    assert clusters[0]["uncertainty"]["causal"]["status"] == "unresolved"
    assert clusters[0]["uncertainty"]["severity"]["status"] == "unresolved"
    assert "turnaway" not in clusters[0]["measured_access_consequence"]


@pytest.mark.parametrize("uncertainty_kind", ["condition"])
def test_unresolved_condition_blocks_confirmation(tmp_path: Path, uncertainty_kind: str) -> None:
    parsed = parse_aggregate_handoff(_aggregate(_envelope("run-1", [_finding()])))
    spec = _cluster_spec(parsed)
    spec["clusters"][0]["uncertainty"][uncertainty_kind] = {
        "status": "unresolved",
        "note": "It is not established whether service was actually reduced.",
    }

    with pytest.raises(FoodLineHistoricalRecoveryError, match="condition uncertainty blocks confirmation"):
        validate_clusters(spec, parsed, build_reconciliation(tmp_path, None, parsed["sources"]), root=tmp_path, pages_root=None)


def test_risk_only_announcement_cannot_be_confirmed(tmp_path: Path) -> None:
    parsed = parse_aggregate_handoff(_aggregate(_envelope("run-1", [_finding()])))
    spec = _cluster_spec(parsed)
    spec["clusters"][0]["measured_access_consequence"] = {
        "type": "risk_or_mitigation_only",
        "description": "",
        "measurement": "",
        "supporting_finding_ids": [],
    }

    with pytest.raises(FoodLineHistoricalRecoveryError, match="risk-only development cannot be confirmed"):
        validate_clusters(spec, parsed, build_reconciliation(tmp_path, None, parsed["sources"]), root=tmp_path, pages_root=None)


def test_generic_insufficient_evidence_disposition_is_rejected(tmp_path: Path) -> None:
    parsed = parse_aggregate_handoff(_aggregate(_envelope("run-1", [_finding()])))
    spec = _cluster_spec(parsed, disposition="deferred_specific_evidence_gap")
    spec["clusters"][0]["disposition_reason"] = "Insufficient evidence"
    spec["clusters"][0]["unresolved_requirement"] = "A dated service schedule from the named pantry."

    with pytest.raises(FoodLineHistoricalRecoveryError, match="event-specific evidence or rule"):
        validate_clusters(
            spec,
            parsed,
            build_reconciliation(tmp_path, None, parsed["sources"]),
            root=tmp_path,
            pages_root=None,
        )


def test_exact_public_url_is_reconciled_and_required_for_already_published(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-1", [_finding()]))
    parsed = parse_aggregate_handoff(raw)
    public = tmp_path / "output" / "site" / "food-line" / "editions" / "2026-08-10" / "index.html"
    public.parent.mkdir(parents=True)
    public.write_text('<a href="https://news.example.org/pantry">Source</a>', encoding="utf-8")
    reconciliation = build_reconciliation(tmp_path, None, parsed["sources"])
    spec = _cluster_spec(parsed, disposition="already_published")
    spec["clusters"][0]["prior_publication_match"] = {"status": "exact_source_url"}

    clusters = validate_clusters(spec, parsed, reconciliation, root=tmp_path, pages_root=None)

    assert reconciliation["exact_url_match_counts"]["source_site_output"] == 1
    assert clusters[0]["proposed_disposition"] == "already_published"


def test_generated_only_url_is_not_treated_as_published(tmp_path: Path) -> None:
    parsed = parse_aggregate_handoff(_aggregate(_envelope("run-1", [_finding()])))
    generated = tmp_path / "output" / "dispatches" / "food-line" / "editions" / "2026-08-10" / "index.html"
    generated.parent.mkdir(parents=True)
    generated.write_text('<a href="https://news.example.org/pantry">Source</a>', encoding="utf-8")

    reconciliation = build_reconciliation(tmp_path, None, parsed["sources"])

    assert reconciliation["exact_url_match_counts"]["source_generated_output"] == 1
    assert reconciliation["sources"][0]["published_exact_url_match"] is False


def test_all_findings_must_be_assigned_exactly_once(tmp_path: Path) -> None:
    raw = _aggregate(
        _envelope(
            "run-1",
            [
                _finding(),
                _finding(
                    source_url="https://news.example.org/second",
                    canonical_source_url="https://news.example.org/second",
                    title="Second event",
                ),
            ],
        )
    )
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    spec["clusters"][0]["finding_ids"] = spec["clusters"][0]["finding_ids"][:1]
    spec["clusters"][0]["measured_access_consequence"]["supporting_finding_ids"] = spec["clusters"][0]["finding_ids"]

    with pytest.raises(FoodLineHistoricalRecoveryError, match="assign every retained finding exactly once"):
        validate_clusters(spec, parsed, build_reconciliation(tmp_path, None, parsed["sources"]), root=tmp_path, pages_root=None)


def test_private_import_is_content_addressed_and_exact_replay_is_nonmutating(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-1", [_finding()]), malformed=True)
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    public_marker = tmp_path / "output" / "site" / "food-line" / "index.html"
    public_marker.parent.mkdir(parents=True)
    public_marker.write_text("unchanged", encoding="utf-8")
    artifacts = build_recovery(
        tmp_path,
        input_path,
        spec_path,
        pages_root=None,
        captured_at="2026-09-01T00:00:00Z",
    )

    first = import_recovery(tmp_path, artifacts, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()))
    recovery = Path(first["recovery_path"])
    before = {path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns) for path in recovery.iterdir()}
    replay_artifacts = build_recovery(
        tmp_path,
        input_path,
        spec_path,
        pages_root=None,
        captured_at="2026-09-01T00:00:00Z",
    )
    second = import_recovery(tmp_path, replay_artifacts, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()))
    after = {path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns) for path in recovery.iterdir()}

    assert first["status"] == "imported"
    assert second["status"] == "idempotent_noop"
    assert artifacts["live_site_reconciliation_report.json"] == replay_artifacts["live_site_reconciliation_report.json"]
    assert replay_artifacts["live_site_reconciliation_report.json"]["exact_url_match_counts"]["historical_agent_records"] == 0
    assert before == after
    assert public_marker.read_text(encoding="utf-8") == "unchanged"
    assert not (tmp_path / "data" / "dispatches" / "food-line" / "agent-intake").exists()
    raw_archive = json.loads((recovery / "raw_archive.json").read_text(encoding="utf-8"))
    assert base64.b64decode(raw_archive["raw_bytes_base64"]) == raw
    assert json.loads((recovery / "recovery_manifest.json").read_text(encoding="utf-8"))["publication_approval"] is False


def test_production_shaped_437_url_replay_excludes_only_itself_and_preserves_bytes(tmp_path: Path) -> None:
    findings = [
        _finding(
            title=f"Historical pressure finding {index}",
            source_url=f"https://news.example.org/historical/{index}?utm_source=watch",
            canonical_source_url=f"https://news.example.org/historical/{index}?utm_source=watch",
            exact_supporting_passage=f"Pantry service reduction {index} was directly reported.",
        )
        for index in range(437)
    ]
    raw = _aggregate(_envelope("run-437", findings))
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    pages_root = tmp_path / "configured-pages"
    pages_root.mkdir()
    pages_marker = pages_root / "unchanged.txt"
    pages_marker.write_text("unchanged", encoding="utf-8")

    artifacts = build_recovery(
        tmp_path,
        input_path,
        spec_path,
        pages_root=pages_root,
        captured_at="2026-09-01T00:00:00Z",
    )
    first = import_recovery(tmp_path, artifacts, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()))
    recovery = Path(first["recovery_path"])
    before = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
        for path in recovery.iterdir()
    }

    replay_artifacts = build_recovery(
        tmp_path,
        input_path,
        spec_path,
        pages_root=pages_root,
        captured_at="2026-09-01T00:00:00Z",
    )
    replay = import_recovery(
        tmp_path,
        replay_artifacts,
        cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()),
    )
    after = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
        for path in recovery.iterdir()
    }

    assert parsed["unique_canonical_source_count"] == 437
    assert artifacts["live_site_reconciliation_report.json"]["exact_url_match_counts"]["historical_agent_records"] == 0
    assert replay_artifacts["live_site_reconciliation_report.json"]["exact_url_match_counts"]["historical_agent_records"] == 0
    assert artifacts["live_site_reconciliation_report.json"] == replay_artifacts["live_site_reconciliation_report.json"]
    assert first["status"] == "imported"
    assert replay["status"] == "idempotent_noop"
    assert before == after
    assert pages_marker.read_text(encoding="utf-8") == "unchanged"
    assert replay_artifacts["priority_confirmed_candidates.json"]["publication_approval"] is False
    assert not (tmp_path / "data" / "universal_events" / "publication-state").exists()
    assert not (tmp_path / "output").exists()


def test_replay_preserves_prior_siblings_parent_and_lookalike_nested_records(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-current", [_finding()]))
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    recoveries = tmp_path / "data" / "agent-history" / "food-line" / "recoveries"
    sibling = recoveries / "sha256-11111111111111111111111111111111"
    lookalike_nested = recoveries / f"sha256-{parsed['input_sha256'][:32]}-lookalike" / "nested"
    sibling.mkdir(parents=True)
    lookalike_nested.mkdir(parents=True)
    (recoveries / "parent-record.json").write_text(
        json.dumps({"url": "https://news.example.org/pantry"}), encoding="utf-8"
    )
    (sibling / "prior.json").write_text(
        json.dumps({"url": "https://news.example.org/pantry"}), encoding="utf-8"
    )
    (lookalike_nested / "record.json").write_text(
        json.dumps({"url": "https://news.example.org/pantry"}), encoding="utf-8"
    )

    artifacts = build_recovery(
        tmp_path, input_path, spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z"
    )
    matches_before = artifacts["live_site_reconciliation_report.json"]["sources"][0]["matches"][
        "historical_agent_records"
    ]
    first = import_recovery(tmp_path, artifacts, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()))
    replay_artifacts = build_recovery(
        tmp_path, input_path, spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z"
    )
    matches_after = replay_artifacts["live_site_reconciliation_report.json"]["sources"][0]["matches"][
        "historical_agent_records"
    ]
    replay = import_recovery(
        tmp_path, replay_artifacts, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes())
    )

    assert first["status"] == "imported"
    assert replay["status"] == "idempotent_noop"
    assert matches_before == matches_after
    assert len(matches_after) == 3
    assert str(recoveries / "parent-record.json") in matches_after
    assert str(sibling / "prior.json") in matches_after
    assert str(lookalike_nested / "record.json") in matches_after
    current_target = Path(first["recovery_path"])
    assert all(not Path(path).is_relative_to(current_target) for path in matches_after)


def test_later_independent_recovery_remains_visible_and_causes_drift(tmp_path: Path) -> None:
    first_raw = _aggregate(_envelope("run-first", [_finding()]))
    first_parsed = parse_aggregate_handoff(first_raw)
    first_spec = _cluster_spec(first_parsed)
    first_input, first_spec_path = _write_input_and_spec(tmp_path / "first", first_raw, first_spec)
    first_artifacts = build_recovery(
        tmp_path, first_input, first_spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z"
    )
    import_recovery(tmp_path, first_artifacts, cluster_spec_sha256=sha256_bytes(first_spec_path.read_bytes()))

    later_raw = _aggregate(_envelope("run-later", [_finding()]))
    later_parsed = parse_aggregate_handoff(later_raw)
    later_spec = _cluster_spec(later_parsed)
    later_input, later_spec_path = _write_input_and_spec(tmp_path / "later", later_raw, later_spec)
    later_artifacts = build_recovery(
        tmp_path, later_input, later_spec_path, pages_root=None, captured_at="2026-09-01T01:00:00Z"
    )
    import_recovery(tmp_path, later_artifacts, cluster_spec_sha256=sha256_bytes(later_spec_path.read_bytes()))

    drifted_first = build_recovery(
        tmp_path, first_input, first_spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z"
    )

    assert later_artifacts["live_site_reconciliation_report.json"]["exact_url_match_counts"]["historical_agent_records"] == 1
    assert drifted_first["live_site_reconciliation_report.json"]["exact_url_match_counts"]["historical_agent_records"] == 1
    with pytest.raises(FoodLineHistoricalRecoveryError, match="conflicting replay"):
        import_recovery(
            tmp_path,
            drifted_first,
            cluster_spec_sha256=sha256_bytes(first_spec_path.read_bytes()),
        )


def test_tampered_current_artifact_still_fails_replay(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-tamper", [_finding()]))
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    artifacts = build_recovery(
        tmp_path, input_path, spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z"
    )
    first = import_recovery(tmp_path, artifacts, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()))
    replay_artifacts = build_recovery(
        tmp_path, input_path, spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z"
    )
    tampered = Path(first["recovery_path"]) / "normalized_findings.json"
    tampered.write_bytes(tampered.read_bytes() + b" ")

    with pytest.raises(FoodLineHistoricalRecoveryError, match="artifact drifted"):
        import_recovery(
            tmp_path,
            replay_artifacts,
            cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()),
        )


@pytest.mark.parametrize(
    "invalid_digest",
    ["../outside", "A" * 64, "0" * 63, "0" * 64 + "/nested"],
)
def test_recovery_identity_rejects_traversal_case_alias_and_malformed_hashes(
    tmp_path: Path, invalid_digest: str
) -> None:
    with pytest.raises(FoodLineHistoricalRecoveryError, match="64 lowercase hexadecimal"):
        _recovery_directory(tmp_path, invalid_digest)


def test_current_recovery_identity_rejects_reparse_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "a" * 64
    recoveries = tmp_path / "data" / "agent-history" / "food-line" / "recoveries"
    recoveries.mkdir(parents=True)
    import bluefern_dispatches.food_line_historical_recovery as recovery_module

    original = recovery_module._is_reparse_point
    monkeypatch.setattr(
        recovery_module,
        "_is_reparse_point",
        lambda path: True if path == recoveries else original(path),
    )

    with pytest.raises(FoodLineHistoricalRecoveryError, match="symlink or junction"):
        _validated_current_recovery_identity(tmp_path, digest)


def test_current_recovery_identity_rejects_reparse_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bluefern_dispatches.food_line_historical_recovery as recovery_module

    original = recovery_module._is_reparse_point
    monkeypatch.setattr(
        recovery_module,
        "_is_reparse_point",
        lambda path: True if path == tmp_path else original(path),
    )

    with pytest.raises(FoodLineHistoricalRecoveryError, match="existing real directory"):
        _validated_current_recovery_identity(tmp_path, "f" * 64)


def test_current_recovery_identity_rejects_reparse_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "d" * 64
    target = tmp_path / "data" / "agent-history" / "food-line" / "recoveries" / f"sha256-{digest[:32]}"
    target.mkdir(parents=True)
    (target / "recovery_manifest.json").write_text(
        json.dumps({"schema_version": "food_line_historical_recovery_v1", "input_sha256": digest}),
        encoding="utf-8",
    )
    import bluefern_dispatches.food_line_historical_recovery as recovery_module

    original = recovery_module._is_reparse_point
    monkeypatch.setattr(
        recovery_module,
        "_is_reparse_point",
        lambda path: True if path == target else original(path),
    )

    with pytest.raises(FoodLineHistoricalRecoveryError, match="symlink or junction"):
        _validated_current_recovery_identity(tmp_path, digest)


@pytest.mark.skipif(os.name != "nt", reason="Windows case aliases are case-insensitive")
def test_current_recovery_identity_rejects_noncanonical_case_alias(tmp_path: Path) -> None:
    digest = "e" * 64
    recovery_root = tmp_path / "data" / "agent-history" / "food-line" / "recoveries"
    aliased = recovery_root / f"SHA256-{digest[:32]}"
    aliased.mkdir(parents=True)
    (aliased / "recovery_manifest.json").write_text(
        json.dumps({"schema_version": "food_line_historical_recovery_v1", "input_sha256": digest}),
        encoding="utf-8",
    )

    with pytest.raises(FoodLineHistoricalRecoveryError, match="case alias"):
        _validated_current_recovery_identity(tmp_path, digest)


def test_existing_target_must_bind_exact_input_identity(tmp_path: Path) -> None:
    digest = "b" * 64
    target = tmp_path / "data" / "agent-history" / "food-line" / "recoveries" / f"sha256-{digest[:32]}"
    target.mkdir(parents=True)
    (target / "recovery_manifest.json").write_text(
        json.dumps({"schema_version": "food_line_historical_recovery_v1", "input_sha256": "c" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(FoodLineHistoricalRecoveryError, match="does not bind"):
        _validated_current_recovery_identity(tmp_path, digest)


def test_reconciliation_exposes_no_arbitrary_path_exclusion(tmp_path: Path) -> None:
    parsed = parse_aggregate_handoff(_aggregate(_envelope("run-1", [_finding()])))

    with pytest.raises(TypeError):
        build_reconciliation(  # type: ignore[call-arg]
            tmp_path,
            None,
            parsed["sources"],
            exclude_path=tmp_path / "arbitrary",
        )


def test_disaster_losses_use_tier_four_and_no_tier_five_is_emitted(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-disaster", [_finding(pressure_type="disaster_household_food_loss")]))
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)

    artifacts = build_recovery(
        tmp_path, input_path, spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z"
    )
    priorities = [
        candidate["priority"]
        for candidate in artifacts["priority_confirmed_candidates.json"]["candidates"]
    ]
    event_priorities = [cluster["priority"] for cluster in artifacts["event_cluster_manifest.json"]["clusters"]]

    assert CONSEQUENCE_PRIORITIES["disaster_household_food_loss"] == 4
    assert set(CONSEQUENCE_PRIORITIES.values()) == {1, 2, 3, 4}
    assert priorities == [4]
    assert 5 not in priorities
    assert 5 not in event_priorities


def test_five_tier_migration_is_out_of_place_exactly_replayable_and_non_authorizing(
    tmp_path: Path,
) -> None:
    raw = _aggregate(
        _envelope(
            "run-migrate",
            [_finding(pressure_type="disaster_household_food_loss")],
        )
    )
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    spec = _cluster_spec(parsed)
    spec["run_month"] = "2026-08"
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    earlier = tmp_path / "data" / "agent-history" / "food-line" / "recoveries" / "sha256-earlier"
    earlier.mkdir(parents=True)
    (earlier / "record.json").write_text(
        json.dumps({"url": "https://news.example.org/pantry"}), encoding="utf-8"
    )
    pages = tmp_path / "configured-pages"
    pages.mkdir()
    pages_marker = pages / "unchanged.txt"
    pages_marker.write_text("unchanged", encoding="utf-8")
    predecessor = _import_five_tier_predecessor(
        tmp_path,
        input_path,
        spec_path,
        run_month="2026-08",
    )
    predecessor_path = Path(predecessor["recovery_path"])
    predecessor_before = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_size, path.stat().st_mtime_ns)
        for path in predecessor_path.iterdir()
    }

    first = migrate_recovery_to_four_tiers(
        **_migration_arguments(tmp_path, input_path, spec_path, predecessor["artifact_set_sha256"])
    )
    successor_path = Path(first["recovery_path"])
    successor_before = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_size, path.stat().st_mtime_ns)
        for path in successor_path.iterdir()
    }
    replay = migrate_recovery_to_four_tiers(
        **_migration_arguments(tmp_path, input_path, spec_path, predecessor["artifact_set_sha256"])
    )
    successor_after = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_size, path.stat().st_mtime_ns)
        for path in successor_path.iterdir()
    }
    predecessor_after = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_size, path.stat().st_mtime_ns)
        for path in predecessor_path.iterdir()
    }

    assert first["status"] == "migrated"
    assert replay["status"] == "idempotent_noop"
    assert first["successor_identity_sha256"] == replay["successor_identity_sha256"]
    assert first["artifact_set_sha256"] == replay["artifact_set_sha256"]
    assert predecessor_before == predecessor_after
    assert successor_before == successor_after
    assert predecessor_path != successor_path
    manifest = json.loads((successor_path / "recovery_manifest.json").read_text(encoding="utf-8"))
    assert manifest["predecessor"]["artifact_set_sha256"] == predecessor["artifact_set_sha256"]
    assert manifest["priority_policy_transition"]["from"] == FIVE_TIER_PRIORITY_POLICY
    assert manifest["priority_policy_transition"]["to"] == FOUR_TIER_PRIORITY_POLICY
    assert manifest["priority_policy_transition"]["tier_counts"]["event_cluster_manifest.json"] == {
        "predecessor": {"5": 1},
        "successor": {"4": 1},
    }
    successor_events = json.loads((successor_path / "event_cluster_manifest.json").read_text(encoding="utf-8"))
    predecessor_reconciliation = json.loads(
        (predecessor_path / "live_site_reconciliation_report.json").read_text(encoding="utf-8")
    )
    successor_reconciliation = json.loads(
        (successor_path / "live_site_reconciliation_report.json").read_text(encoding="utf-8")
    )
    assert successor_events["clusters"][0]["priority"] == 4
    assert predecessor_reconciliation == successor_reconciliation
    historical_matches = successor_reconciliation["sources"][0]["matches"]["historical_agent_records"]
    assert historical_matches == [str(earlier / "record.json")]
    assert pages_marker.read_text(encoding="utf-8") == "unchanged"
    assert first["publication_approval"] is False
    assert first["queue_items_created"] == 0
    assert first["pages_files_written"] == 0
    assert not (tmp_path / "data" / "dispatches" / "food-line" / "agent-intake").exists()
    assert not (tmp_path / "data" / "universal_events" / "publication-state").exists()
    assert not (tmp_path / "output").exists()


def test_production_shaped_migration_preserves_totals_and_expected_tier_transition(tmp_path: Path) -> None:
    findings = []
    for index in range(505):
        url_index = index if index < 437 else index - 437
        findings.append(
            _finding(
                title=f"August historical finding {index}",
                source_url=f"https://news.example.org/august/{url_index}?utm_source=watch",
                canonical_source_url=f"https://news.example.org/august/{url_index}?utm_source=watch",
                exact_supporting_passage=f"Measured historical food pressure {index} was reported.",
            )
        )
    raw = _aggregate(_envelope("run-production-shaped", findings))
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    assert parsed["retained_finding_count"] == 505
    assert parsed["unique_canonical_source_count"] == 437
    spec = _production_shaped_spec(parsed)
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    finding_by_id = {item["finding_id"]: item for item in parsed["findings"]}
    published_urls = [
        finding_by_id[cluster["primary_finding_id"]]["canonical_source_url"]
        for cluster in spec["clusters"][-2:]
    ]
    public = tmp_path / "output" / "site" / "food-line" / "history.html"
    public.parent.mkdir(parents=True)
    public.write_text("\n".join(published_urls), encoding="utf-8")
    predecessor = _import_five_tier_predecessor(
        tmp_path,
        input_path,
        spec_path,
        run_month="2026-08",
    )

    result = migrate_recovery_to_four_tiers(
        **_migration_arguments(tmp_path, input_path, spec_path, predecessor["artifact_set_sha256"])
    )
    manifest = json.loads((Path(result["recovery_path"]) / "recovery_manifest.json").read_text(encoding="utf-8"))
    tiers = manifest["priority_policy_transition"]["tier_counts"]

    assert result["status"] == "migrated"
    assert result["recovery_totals"] == {
        "retained_findings": 505,
        "unique_canonical_urls": 437,
        "event_clusters": 414,
        "dispositions": {
            "already_published": 2,
            "confirmed_historical_review_candidate": 320,
            "deferred_specific_evidence_gap": 67,
            "duplicate_or_corroboration": 10,
            "excluded_under_existing_rules": 15,
        },
    }
    assert tiers["priority_confirmed_candidates.json"] == {
        "predecessor": {"1": 158, "2": 20, "3": 57, "4": 64, "5": 21},
        "successor": {"1": 158, "2": 20, "3": 57, "4": 85},
    }
    assert tiers["event_cluster_manifest.json"]["predecessor"]["5"] == 22
    assert tiers["event_cluster_manifest.json"]["successor"].get("5") is None
    assert tiers["event_cluster_manifest.json"]["predecessor"]["5"] - tiers[
        "priority_confirmed_candidates.json"
    ]["predecessor"]["5"] == 1


def test_migration_fails_for_predecessor_drift_unexpected_files_and_binding_drift(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-guard", [_finding(pressure_type="disaster_household_food_loss")]))
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    spec = _cluster_spec(parsed)
    spec["run_month"] = "2026-08"
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    predecessor = _import_five_tier_predecessor(
        tmp_path, input_path, spec_path, run_month="2026-08"
    )
    arguments = _migration_arguments(tmp_path, input_path, spec_path, predecessor["artifact_set_sha256"])
    predecessor_path = Path(predecessor["recovery_path"])
    unexpected = predecessor_path / "unexpected.json"
    unexpected.write_text("{}", encoding="utf-8")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="file inventory drifted"):
        migrate_recovery_to_four_tiers(**arguments)
    unexpected.unlink()
    artifact = predecessor_path / "normalized_findings.json"
    original = artifact.read_bytes()
    artifact.write_bytes(original + b" ")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="artifact hash drifted"):
        migrate_recovery_to_four_tiers(**arguments)
    artifact.write_bytes(original)
    spec_path.write_bytes(spec_path.read_bytes() + b" ")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="cluster-specification hash drifted"):
        migrate_recovery_to_four_tiers(**arguments)


def test_migration_semantic_audit_rejects_any_nonpriority_change(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-semantic", [_finding(pressure_type="disaster_household_food_loss")]))
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    spec = _cluster_spec(parsed)
    spec["run_month"] = "2026-08"
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    predecessor_result = _import_five_tier_predecessor(
        tmp_path, input_path, spec_path, run_month="2026-08"
    )
    predecessor_path = Path(predecessor_result["recovery_path"])
    predecessor = {
        name: json.loads((predecessor_path / name).read_text(encoding="utf-8"))
        for name in (
            "raw_archive.json",
            "normalized_unique_sources.json",
            "normalized_findings.json",
            "event_cluster_manifest.json",
            "live_site_reconciliation_report.json",
            "disposition_matrix.json",
            "import_validation_report.json",
            "priority_confirmed_candidates.json",
        )
    }
    successor = json.loads(json.dumps(predecessor))
    successor["event_cluster_manifest.json"]["clusters"][0]["priority"] = 4
    successor["priority_confirmed_candidates.json"]["candidates"][0]["priority"] = 4
    successor["event_cluster_manifest.json"]["clusters"][0]["organization_display"] = "Changed"

    with pytest.raises(FoodLineHistoricalRecoveryError, match="unapproved semantics"):
        _audit_four_tier_semantic_diff(predecessor, successor)


def test_migration_atomic_failure_leaves_no_successor_or_predecessor_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _aggregate(_envelope("run-atomic", [_finding(pressure_type="disaster_household_food_loss")]))
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    spec = _cluster_spec(parsed)
    spec["run_month"] = "2026-08"
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    predecessor = _import_five_tier_predecessor(
        tmp_path, input_path, spec_path, run_month="2026-08"
    )
    predecessor_path = Path(predecessor["recovery_path"])
    before = {path.name: sha256_bytes(path.read_bytes()) for path in predecessor_path.iterdir()}
    import bluefern_dispatches.food_line_historical_recovery as recovery_module

    original_replace = recovery_module.os.replace

    def fail_directory_replace(source, destination):
        if Path(destination).parent.name == "recovery-migrations":
            raise OSError("synthetic final replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(recovery_module.os, "replace", fail_directory_replace)
    with pytest.raises(FoodLineHistoricalRecoveryError, match="atomic successor creation failed"):
        migrate_recovery_to_four_tiers(
            **_migration_arguments(tmp_path, input_path, spec_path, predecessor["artifact_set_sha256"])
        )

    migration_root = tmp_path / "data" / "agent-history" / "food-line" / "recovery-migrations"
    assert list(migration_root.iterdir()) == []
    assert before == {path.name: sha256_bytes(path.read_bytes()) for path in predecessor_path.iterdir()}


def test_migration_rejects_reparse_predecessor_and_conflicting_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _aggregate(_envelope("run-reparse", [_finding(pressure_type="disaster_household_food_loss")]))
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    spec = _cluster_spec(parsed)
    spec["run_month"] = "2026-08"
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    predecessor = _import_five_tier_predecessor(
        tmp_path, input_path, spec_path, run_month="2026-08"
    )
    arguments = _migration_arguments(tmp_path, input_path, spec_path, predecessor["artifact_set_sha256"])
    predecessor_path = Path(predecessor["recovery_path"])
    import bluefern_dispatches.food_line_historical_recovery as recovery_module

    original = recovery_module._is_reparse_point
    monkeypatch.setattr(
        recovery_module,
        "_is_reparse_point",
        lambda path: True if path == predecessor_path else original(path),
    )
    with pytest.raises(FoodLineHistoricalRecoveryError, match="symlink or junction"):
        migrate_recovery_to_four_tiers(**arguments)
    monkeypatch.setattr(recovery_module, "_is_reparse_point", original)
    created = migrate_recovery_to_four_tiers(**arguments)
    successor_artifact = Path(created["recovery_path"]) / "normalized_findings.json"
    successor_artifact.write_bytes(successor_artifact.read_bytes() + b" ")
    with pytest.raises(FoodLineHistoricalRecoveryError, match="conflicting migration replay"):
        migrate_recovery_to_four_tiers(**arguments)


def test_migration_rejects_traversal_reparse_root_and_arbitrary_predecessor_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _aggregate(_envelope("run-paths", [_finding(pressure_type="disaster_household_food_loss")]))
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    spec = _cluster_spec(parsed)
    spec["run_month"] = "2026-08"
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    predecessor = _import_five_tier_predecessor(
        tmp_path, input_path, spec_path, run_month="2026-08"
    )
    arguments = _migration_arguments(tmp_path, input_path, spec_path, predecessor["artifact_set_sha256"])
    traversal_input = input_path.parent / "nested" / ".." / input_path.name
    with pytest.raises(FoodLineHistoricalRecoveryError, match="path traversal"):
        migrate_recovery_to_four_tiers(**{**arguments, "input_path": traversal_input})
    with pytest.raises(TypeError):
        migrate_recovery_to_four_tiers(  # type: ignore[call-arg]
            **arguments,
            predecessor_path=tmp_path / "arbitrary",
        )

    import bluefern_dispatches.food_line_historical_recovery as recovery_module

    original = recovery_module._is_reparse_point
    monkeypatch.setattr(
        recovery_module,
        "_is_reparse_point",
        lambda path: True if path.name == "recovery-migrations" else original(path),
    )
    with pytest.raises(FoodLineHistoricalRecoveryError, match="symlink or junction"):
        migrate_recovery_to_four_tiers(**arguments)


def test_cli_migrate_requires_bound_identity_and_replays_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _aggregate(_envelope("run-cli-migrate", [_finding(pressure_type="disaster_household_food_loss")]))
    parsed = parse_aggregate_handoff(raw, run_month="2026-08")
    spec = _cluster_spec(parsed)
    spec["run_month"] = "2026-08"
    spec["clusters"][0]["measured_access_consequence"]["type"] = "disaster_household_food_loss"
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    predecessor = _import_five_tier_predecessor(
        tmp_path, input_path, spec_path, run_month="2026-08"
    )
    import scripts.import_food_line_historical_recovery as cli_module

    monkeypatch.setattr(cli_module, "validate_migration_implementation_commit", lambda *_: None)
    arguments = [
        "migrate",
        "--input",
        str(input_path),
        "--cluster-spec",
        str(spec_path),
        "--captured-at",
        "2026-09-01T00:00:00Z",
        "--run-month",
        "2026-08",
        "--repo-root",
        str(tmp_path),
        "--predecessor-artifact-set",
        predecessor["artifact_set_sha256"],
        "--implementation-source-commit",
        "b" * 40,
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    before = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
        for path in Path(first["recovery_path"]).iterdir()
    }
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    after = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
        for path in Path(first["recovery_path"]).iterdir()
    }

    assert first["status"] == "migrated"
    assert second["status"] == "idempotent_noop"
    assert before == after


def test_conflicting_replay_fails_closed(tmp_path: Path) -> None:
    raw = _aggregate(_envelope("run-1", [_finding()]))
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    artifacts = build_recovery(tmp_path, input_path, spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z")
    import_recovery(tmp_path, artifacts, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()))
    spec["clusters"][0]["disposition_reason"] = "A conflicting review reason."
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    changed = build_recovery(tmp_path, input_path, spec_path, pages_root=None, captured_at="2026-09-01T00:00:00Z")

    with pytest.raises(FoodLineHistoricalRecoveryError, match="conflicting replay"):
        import_recovery(tmp_path, changed, cluster_spec_sha256=sha256_bytes(spec_path.read_bytes()))


def test_cli_import_rebuilds_and_returns_idempotent_noop_on_exact_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = _aggregate(_envelope("run-cli-replay", [_finding()]))
    parsed = parse_aggregate_handoff(raw)
    spec = _cluster_spec(parsed)
    input_path, spec_path = _write_input_and_spec(tmp_path, raw, spec)
    arguments = [
        "import",
        "--input",
        str(input_path),
        "--cluster-spec",
        str(spec_path),
        "--captured-at",
        "2026-09-01T00:00:00Z",
        "--repo-root",
        str(tmp_path),
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    recovery = Path(first["recovery_path"])
    before = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
        for path in recovery.iterdir()
    }
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    after = {
        path.name: (sha256_bytes(path.read_bytes()), path.stat().st_mtime_ns)
        for path in recovery.iterdir()
    }

    assert first["status"] == "imported"
    assert second["status"] == "idempotent_noop"
    assert before == after


def test_cli_template_is_limited_to_private_staging(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = _aggregate(_envelope("run-1", [_finding()]))
    input_path = tmp_path / "aggregate.md"
    input_path.write_bytes(raw)
    private_output = tmp_path / "data" / "agent-history-staging" / "food-line" / "clusters.json"

    assert main(["template", "--input", str(input_path), "--repo-root", str(tmp_path), "--template-output", str(private_output)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["template", "--input", str(input_path), "--repo-root", str(tmp_path), "--template-output", str(private_output)]) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["status"] == "template_created"
    assert second["status"] == "idempotent_noop"
    assert not (tmp_path / "output").exists()


def test_cli_refuses_public_template_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_path = tmp_path / "aggregate.md"
    input_path.write_bytes(_aggregate(_envelope("run-1", [_finding()])))
    public_output = tmp_path / "output" / "site" / "clusters.json"

    assert main(["template", "--input", str(input_path), "--repo-root", str(tmp_path), "--template-output", str(public_output)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert not public_output.exists()
