from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches import event_ledger
from bluefern_dispatches import food_line_discovery_expansion as discovery
from bluefern_dispatches.event_ledger import (
    DEFAULT_LEDGER_PATH,
    classify_food_line_candidate,
    connect_ledger,
    event_record_from_food_line_candidate,
    get_event,
    get_event_sources,
    food_line_event_id,
    inspect_ledger,
    source_observation_from_food_line_candidate,
    upsert_event_record,
    write_food_line_shadow_events,
)


def food_candidate(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "candidate_id": "candidate-1",
        "selected_title": "Rantoul Price Cutter closure limits grocery access",
        "publisher": "Example News",
        "discovered_publisher": "Example News",
        "source_url": "https://example.org/story?utm_source=newsletter",
        "canonical_url": "https://example.org/story",
        "source_published_date": "2026-09-02",
        "state_or_territory": "Illinois",
        "metro": "Rantoul",
        "organization_name": "Price Cutter",
        "pressure_type": "grocery closure",
        "pressure_summary": "Rantoul residents lose a grocery access point.",
        "evidence_text": "The Price Cutter grocery store in Rantoul is closing, leaving residents with fewer nearby grocery options.",
        "evidence_text_basis": "page_text_excerpt",
        "affected_groups": ["residents"],
        "classification_status": "qualified_pressure_signal",
        "candidate_review_status": "needs_review",
        "manual_review_required": True,
        "public_claim_eligible": False,
        "public_claim_blockers": ["manual_review_required"],
        "source_role": "local_news_report",
        "discovery_channel": "direct_rss",
    }
    candidate.update(overrides)
    return candidate


def test_food_line_event_id_is_deterministic_and_url_independent() -> None:
    first = food_candidate(source_url="https://example.org/a")
    second = food_candidate(source_url="https://different.example/story")

    assert food_line_event_id(first) == food_line_event_id(first)
    assert food_line_event_id(first) == food_line_event_id(second)
    assert food_line_event_id(first) == "food-line-rantoul-price-cutter-grocery-closure"


def test_food_line_event_id_is_stable_when_lifecycle_date_becomes_known() -> None:
    announced = food_candidate(source_published_date="2026-09-02", event_date="", closure_date="")
    scheduled = food_candidate(source_published_date="2026-09-10", closure_date="2026-09-30")

    assert food_line_event_id(announced) == food_line_event_id(scheduled)


def test_announced_scheduled_completed_lifecycle_remains_one_event(tmp_path: Path) -> None:
    announced = food_candidate(
        candidate_id="announced",
        source_url="https://example.org/announced",
        canonical_url="https://example.org/announced",
        lifecycle_status="announced",
        closure_date="",
    )
    scheduled = food_candidate(
        candidate_id="scheduled",
        source_url="https://example.org/scheduled",
        canonical_url="https://example.org/scheduled",
        lifecycle_status="scheduled",
        closure_date="2026-09-30",
    )
    completed = food_candidate(
        candidate_id="completed",
        source_url="https://example.org/completed",
        canonical_url="https://example.org/completed",
        lifecycle_status="completed",
        closure_date="2026-09-30",
    )

    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        for candidate in (announced, scheduled, completed):
            event_id = food_line_event_id(candidate)
            discovery_class, reasons = classify_food_line_candidate(conn, candidate, event_id=event_id)
            record = event_record_from_food_line_candidate(candidate, discovery_class=discovery_class, event_id=event_id)
            upsert_event_record(conn, record, candidate=candidate, material_change_reasons=reasons)

        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM event_observations").fetchone()[0] == 3
        classes = [
            row[0]
            for row in conn.execute(
                "SELECT discovery_class FROM event_observations ORDER BY observation_id"
            )
        ]
        assert classes == ["new_development", "meaningful_update", "meaningful_update"]


def test_separate_same_organization_events_can_use_explicit_occurrence_key() -> None:
    first = food_candidate(event_occurrence_key="2026-storm-week-1")
    second = food_candidate(event_occurrence_key="2026-storm-week-2")

    assert food_line_event_id(first) != food_line_event_id(second)


def test_source_observation_preserves_supporting_passage_and_canonicalizes_url() -> None:
    observation = source_observation_from_food_line_candidate(food_candidate())

    assert observation["canonical_source_url"] == "https://example.org/story"
    assert observation["exact_supporting_passage"].startswith("The Price Cutter grocery store")
    assert observation["source_published_at"] == "2026-09-02"


def test_duplicate_source_observation_is_suppressed(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        candidate = food_candidate()
        record = event_record_from_food_line_candidate(candidate, discovery_class="new_development")
        first = upsert_event_record(conn, record, candidate=candidate)
        second = upsert_event_record(conn, record, candidate={**candidate, "candidate_id": "candidate-2"})

        assert first["source_observation_inserted"] is True
        assert second["source_observation_inserted"] is False
        count = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
        assert count == 1


def test_multiple_sources_attach_to_one_event(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        first = food_candidate(source_url="https://example.org/story", canonical_url="https://example.org/story")
        second = food_candidate(source_url="https://local.example/rantoul-price-cutter", canonical_url="https://local.example/rantoul-price-cutter")
        for candidate in (first, second):
            discovery_class, reasons = classify_food_line_candidate(conn, candidate)
            record = event_record_from_food_line_candidate(candidate, discovery_class=discovery_class)
            upsert_event_record(conn, record, candidate=candidate, material_change_reasons=reasons)

        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        source_count = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
        event = get_event(conn, food_line_event_id(first))
        assert event_count == 1
        assert source_count == 2
        assert [source["canonical_source_url"] for source in event["sources"]] == [
            "https://example.org/story",
            "https://local.example/rantoul-price-cutter",
        ]


def test_event_source_history_is_accumulated_without_duplicate_canonical_sources(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        first = food_candidate(source_url="https://example.org/story", canonical_url="https://example.org/story")
        second = food_candidate(source_url="https://local.example/story", canonical_url="https://local.example/story")
        rediscovered_first = food_candidate(
            candidate_id="candidate-rediscovered",
            source_url="https://example.org/story?utm_source=again",
            canonical_url="https://example.org/story",
        )
        for candidate in (first, second, rediscovered_first):
            discovery_class, reasons = classify_food_line_candidate(conn, candidate)
            record = event_record_from_food_line_candidate(candidate, discovery_class=discovery_class)
            upsert_event_record(conn, record, candidate=candidate, material_change_reasons=reasons)

        sources = get_event_sources(conn, food_line_event_id(first))
        event = get_event(conn, food_line_event_id(first))

    assert [source["canonical_source_url"] for source in sources] == [
        "https://example.org/story",
        "https://local.example/story",
    ]
    assert event is not None
    assert [source["canonical_source_url"] for source in event["sources"]] == [
        "https://example.org/story",
        "https://local.example/story",
    ]


def test_new_development_classification(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        discovery_class, reasons = classify_food_line_candidate(conn, food_candidate())

    assert discovery_class == "new_development"
    assert reasons == ["no existing event with same event identity"]


def test_meaningful_update_classification(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        original = food_candidate()
        upsert_event_record(conn, event_record_from_food_line_candidate(original, discovery_class="new_development"), candidate=original)

        updated = food_candidate(
            candidate_id="candidate-update",
            evidence_text="The closure worsened access and affected more residents than first reported.",
            affected_groups=["residents", "seniors"],
        )
        discovery_class, reasons = classify_food_line_candidate(conn, updated)

    assert discovery_class == "meaningful_update"
    assert "affected population changed" in reasons


def test_recycled_article_with_generic_closed_word_is_duplicate_source(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        original = food_candidate(
            evidence_text="The store closed and residents have fewer grocery options.",
            pressure_summary="The store closed and residents have fewer grocery options.",
        )
        upsert_event_record(conn, event_record_from_food_line_candidate(original, discovery_class="new_development"), candidate=original)
        recycled = food_candidate(
            candidate_id="candidate-recycled",
            source_url="https://local.example/recycled",
            canonical_url="https://local.example/recycled",
            evidence_text="The store closed and residents have fewer grocery options.",
            pressure_summary="The store closed and residents have fewer grocery options.",
        )
        discovery_class, reasons = classify_food_line_candidate(conn, recycled)

    assert discovery_class == "duplicate_source"
    assert reasons == ["same event identity without material change"]


def test_status_transition_is_meaningful_update(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        original = food_candidate(lifecycle_status="announced")
        upsert_event_record(conn, event_record_from_food_line_candidate(original, discovery_class="new_development"), candidate=original)
        updated = food_candidate(candidate_id="candidate-scheduled", lifecycle_status="scheduled")
        discovery_class, reasons = classify_food_line_candidate(conn, updated)

    assert discovery_class == "meaningful_update"
    assert "status changed" in reasons


def test_newly_established_closure_date_is_meaningful_update(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        original = food_candidate(event_date="", closure_date="", source_published_date="2026-09-02")
        upsert_event_record(conn, event_record_from_food_line_candidate(original, discovery_class="new_development"), candidate=original)
        updated = food_candidate(candidate_id="candidate-date", closure_date="2026-09-30", source_published_date="2026-09-10")
        discovery_class, reasons = classify_food_line_candidate(conn, updated)

    assert food_line_event_id(original) == food_line_event_id(updated)
    assert discovery_class == "meaningful_update"
    assert "effective date became known" in reasons


def test_materially_increased_affected_population_is_meaningful_update(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        original = food_candidate(affected_groups=["residents"])
        upsert_event_record(conn, event_record_from_food_line_candidate(original, discovery_class="new_development"), candidate=original)
        updated = food_candidate(candidate_id="candidate-population", affected_groups=["residents", "seniors"])
        discovery_class, reasons = classify_food_line_candidate(conn, updated)

    assert discovery_class == "meaningful_update"
    assert "affected population changed" in reasons


def test_duplicate_source_classification(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        original = food_candidate()
        upsert_event_record(conn, event_record_from_food_line_candidate(original, discovery_class="new_development"), candidate=original)
        discovery_class, reasons = classify_food_line_candidate(conn, food_candidate(candidate_id="candidate-duplicate"))

    assert discovery_class == "duplicate_source"
    assert reasons == ["same event identity without material change"]


def test_historical_recovery_classification(tmp_path: Path) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        discovery_class, reasons = classify_food_line_candidate(
            conn,
            food_candidate(source_published_date="2026-08-15"),
            monitoring_start="2026-09-01",
        )

    assert discovery_class == "historical_recovery"
    assert reasons == ["effective date predates live monitoring window"]


def test_null_unknown_timestamps_are_preserved(tmp_path: Path) -> None:
    candidate = food_candidate(source_published_date="", effective_at="", event_date="", closure_date="")
    record = event_record_from_food_line_candidate(candidate, discovery_class="new_development")

    assert record.effective_at is None
    assert record.sources[0]["source_published_at"] is None


def test_transaction_safety_rolls_back_on_observation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with connect_ledger(tmp_path / "ledger.sqlite") as conn:
        candidate = food_candidate()
        record = event_record_from_food_line_candidate(candidate, discovery_class="new_development")
        original_json = event_ledger._json

        def failing_json(value: object) -> str:
            if value == ["force observation failure"]:
                raise RuntimeError("forced observation failure")
            return original_json(value)

        monkeypatch.setattr(event_ledger, "_json", failing_json)
        with pytest.raises(RuntimeError, match="forced observation failure"):
            upsert_event_record(conn, record, candidate=candidate, material_change_reasons=["force observation failure"])

        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        source_count = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
        assert event_count == 0
        assert source_count == 0


def test_write_food_line_shadow_events_records_agent_run_and_summary(tmp_path: Path) -> None:
    result = write_food_line_shadow_events(
        tmp_path,
        [food_candidate(), food_candidate(candidate_id="candidate-2", source_url="https://local.example/story", canonical_url="https://local.example/story")],
        run_id="run-1",
        edition_date="2026-09-02",
    )
    summary = inspect_ledger(tmp_path / DEFAULT_LEDGER_PATH)

    assert result["ok"] is True
    assert result["candidate_count"] == 2
    assert result["events_written"] == 2
    assert summary["event_count"] == 1
    assert summary["discovery_class_counts"] == {"duplicate_source": 1}
    assert summary["latest_observations"]


def test_inspect_ledger_does_not_create_missing_database(tmp_path: Path) -> None:
    ledger_path = tmp_path / DEFAULT_LEDGER_PATH

    summary = inspect_ledger(ledger_path)

    assert summary["event_count"] == 0
    assert not ledger_path.exists()


def manual_fallback_record() -> dict[str, object]:
    return {
        "publisher": "Example News",
        "canonical_url": "https://example.org/story",
        "headline": "Rantoul Price Cutter closure limits grocery access",
        "date": "2026-09-02",
        "location": "Rantoul, Illinois",
        "state_or_territory": "Illinois",
        "metro": "Rantoul",
        "manually_reviewed_summary": "Rantoul residents lose a grocery access point.",
        "pressure_evidence_summary": "The Price Cutter grocery store in Rantoul is closing, leaving residents with fewer nearby grocery options.",
        "pressure_type": "grocery closure",
        "affected_groups": ["residents"],
        "evidence_level": "source_backed",
        "freshness_role": "current",
        "source_role": "local_news_report",
        "limitations": "Fixture for shadow ledger test.",
        "reviewer_or_source_note": "No publication authority.",
        "final_trace_url": "https://example.org/story",
        "extraction_quality": "manual_fallback_reviewed",
    }


def test_shadow_ledger_failure_does_not_break_food_line_discovery_or_change_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discovery, "write_food_line_shadow_events", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ledger down")))

    result = discovery.run_food_line_discovery_expansion(
        tmp_path,
        "2026-09-02",
        manual_fallback_records=[manual_fallback_record()],
        max_queries=0,
        dry_run=False,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["shadow_event_ledger"]["ok"] is False
    assert result["shadow_event_ledger"]["status"] == "failed_nonblocking"
    assert result["shadow_event_ledger"]["error_message"] == "ledger down"
    assert len(candidates) == 1
    assert candidates[0]["selected_title"] == "Rantoul Price Cutter closure limits grocery access"
    assert not (tmp_path / "output" / "site").exists()


def test_food_line_shadow_mode_writes_ledger_without_public_side_effects(tmp_path: Path) -> None:
    result = discovery.run_food_line_discovery_expansion(
        tmp_path,
        "2026-09-02",
        manual_fallback_records=[manual_fallback_record()],
        max_queries=0,
        dry_run=False,
    )

    assert result["ok"] is True
    assert result["shadow_event_ledger"]["ok"] is True
    assert result["shadow_event_ledger"]["shadow_mode"] is True
    assert (tmp_path / DEFAULT_LEDGER_PATH).exists()
    assert not (tmp_path / "output" / "site").exists()
