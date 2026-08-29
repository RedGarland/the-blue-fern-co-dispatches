import json
from pathlib import Path
import pytest

from bluefern_dispatches.adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from bluefern_dispatches.agent_findings import duplicate_key_for, normalize_source_url
from scripts.import_food_line_agent_findings import process, validate_input


def _row(**overrides):
    row = {"title": "Food bank demand rises", "publisher": "Local News", "source_url": "https://example.com/story?utm_source=agent", "source_published_at": "2026-07-27", "exact_supporting_passage": "Food bank demand rose as households faced higher grocery costs.", "summary": "A pressure signal.", "location_name": "Charlotte", "state": "NC", "confidence": "high", "agent_query_context": {"query": "food insecurity"}}
    row.update(overrides)
    return row


def test_urls_and_duplicate_keys_are_stable_and_tracking_free():
    assert normalize_source_url("https://Example.com/story?utm_source=x&fbclid=y") == "https://example.com/story"
    a = duplicate_key_for(canonical_source_url="https://example.com/story", title="A", publisher="P")
    b = duplicate_key_for(canonical_source_url="https://example.com/story", title="A", publisher="P")
    assert a == b


def test_adapter_preserves_passage_and_private_payload_and_pending_review():
    finding = adapt_food_line_agent_output([_row()], agent_name="fixture", agent_run_id="run-1", discovered_at="2026-07-27T00:00:00Z")[0]
    assert finding.exact_supporting_passage != finding.summary
    assert finding.review_status == "pending_review"
    assert finding.raw_agent_payload["title"] == "Food bank demand rises"
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-07-27")
    assert candidate["review_status"] == "pending_review"
    assert candidate["evidence_text"] == finding.exact_supporting_passage


def test_source_published_date_is_accepted_for_intake_freshness():
    payload = _row()
    payload.pop("source_published_at")
    payload["source_published_date"] = "2026-07-27"
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="run-1", discovered_at="2026-07-27T00:00:00Z")[0]
    assert finding.source_published_at == "2026-07-27"
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-07-27")
    assert candidate["published_at"] == "2026-07-27"
    assert candidate["source_published_date"] == "2026-07-27"


def test_retrieved_at_is_accepted_when_publication_dates_are_missing():
    payload = _row()
    payload.pop("source_published_at")
    payload["source_published_date"] = ""
    payload["publication_date"] = ""
    payload["retrieved_at"] = "2026-07-27T08:15:00Z"
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="run-1", discovered_at="2026-07-27T00:00:00Z")[0]
    assert finding.source_published_at == "2026-07-27T08:15:00Z"
    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-07-27")
    assert candidate["published_at"] == "2026-07-27T08:15:00Z"
    assert candidate["source_published_date"] == "2026-07-27"


def test_missing_evidence_and_invalid_url_fail_closed():
    missing = adapt_food_line_agent_output([_row(exact_supporting_passage="")], agent_name="fixture", agent_run_id="run-1")[0]
    invalid = adapt_food_line_agent_output([_row(source_url="http://example.com/story")], agent_name="fixture", agent_run_id="run-1")[0]
    assert not map_finding_to_food_line_candidate(missing, edition_date="2026-07-27")["eligible_for_review"]
    assert "invalid_or_missing_https_url" in map_finding_to_food_line_candidate(invalid, edition_date="2026-07-27")["exclusion_reason"]


def test_dry_run_writes_nothing(tmp_path: Path):
    source = tmp_path / "agent.json"
    source.write_text(json.dumps([_row()]), encoding="utf-8")
    result = process(tmp_path, source, edition_date="2026-07-27", agent_name="fixture", agent_run_id="run-1", dry_run=True)
    assert result["would_write"] is False
    assert not (tmp_path / "data").exists()


def test_import_is_private_and_idempotent_shape(tmp_path: Path):
    source = tmp_path / "agent.json"
    source.write_text(json.dumps([_row(), _row(source_url="https://example.com/story?gclid=2")]), encoding="utf-8")
    first = process(tmp_path, source, edition_date="2026-07-27", agent_name="fixture", agent_run_id="run-1", dry_run=False)
    second = process(tmp_path, source, edition_date="2026-07-27", agent_name="fixture", agent_run_id="run-1", dry_run=False)
    assert first["finding_ids"] == second["finding_ids"]
    assert (tmp_path / "data/dispatches/food-line/agent-intake/2026-07-27/run-1.json").exists()
    assert not (tmp_path / "output").exists()


def _envelope(row):
    return {"schema_version": "food_line_agent_run_v1", "agent_name": "fixture", "agent_run_id": "run-envelope", "started_at": "2026-07-28T00:00:00Z", "completed_at": "2026-07-28T00:01:00Z", "search_window": {"date_from": "2026-07-27", "date_to": "2026-07-28"}, "findings": [row], "coverage_notes": "synthetic test"}


def test_canonical_envelope_validates_and_records_duplicate_findings(tmp_path: Path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_envelope(_row())), encoding="utf-8")
    result = validate_input(path)
    assert result["valid"] is True
    duplicate = _envelope(_row()); duplicate["findings"].append(_row())
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    result = validate_input(path)
    assert result["valid"] is False and result["duplicate_findings"]


def test_invalid_envelope_and_dry_run_do_not_archive(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    assert validate_input(path)["valid"] is False
    inbox = tmp_path / "data/dispatches/food-line/agent-inbox"
    inbox.mkdir(parents=True)
    source = inbox / "run.json"
    source.write_text(json.dumps(_envelope(_row())), encoding="utf-8")
    result = process(tmp_path, source, edition_date="2026-07-28", agent_name="fixture", agent_run_id="run-envelope", dry_run=True)
    assert result["would_write"] is False
    assert not (inbox / "processed").exists()


def test_import_records_hash_and_preserves_inbox_file(tmp_path: Path):
    inbox = tmp_path / "data/dispatches/food-line/agent-inbox"; inbox.mkdir(parents=True)
    source = inbox / "run.json"; source.write_text(json.dumps(_envelope(_row())), encoding="utf-8")
    result = process(tmp_path, source, edition_date="2026-07-28", agent_name="fixture", agent_run_id="run-envelope", dry_run=False)
    artifact = json.loads((tmp_path / "data/dispatches/food-line/agent-intake/2026-07-28/run-envelope.json").read_text(encoding="utf-8"))
    assert artifact["input_sha256"] == result["input_sha256"]
    assert source.exists()
    assert (inbox / "processed/2026-07-28/run.json").exists()


@pytest.mark.parametrize(
    ("context", "expected"),
    [({"query": "food access"}, {"query": "food access"}), ("literal query text", "literal query text"), (None, {})],
)
def test_agent_query_context_supported_types(context, expected):
    payload = _row(agent_query_context=context)
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="run-1", discovered_at="2026-07-27T00:00:00Z")[0]
    assert finding.agent_query_context == expected


def test_agent_query_context_omitted_becomes_empty_mapping():
    payload = _row(); payload.pop("agent_query_context")
    finding = adapt_food_line_agent_output([payload], agent_name="fixture", agent_run_id="run-1", discovered_at="2026-07-27T00:00:00Z")[0]
    assert finding.agent_query_context == {}


@pytest.mark.parametrize("context", [[], ["query"], 42, 3.14])
def test_agent_query_context_unsupported_types_fail_closed(context):
    with pytest.raises(ValueError, match="agent_query_context must be a mapping, string, or null"):
        adapt_food_line_agent_output([_row(agent_query_context=context)], agent_name="fixture", agent_run_id="run-1")


def test_explicit_pantries_that_cannot_continue_operating_are_service_reductions():
    passage = (
        "The pantry provided food to an average of 960 people and distributed approximately 34,000 pounds "
        "of food each month. Building conditions and repair costs made it impossible to continue operating "
        "the pantry safely and sustainably."
    )
    finding = adapt_food_line_agent_output(
        [_row(exact_supporting_passage=passage, source_published_at="2026-07-28T16:26:00-05:00")],
        agent_name="fixture",
        agent_run_id="run-superior",
        discovered_at="2026-07-31T03:44:00Z",
    )[0]

    candidate = map_finding_to_food_line_candidate(finding, edition_date="2026-07-31")

    assert candidate["pressure_signal"] is True
    assert candidate["pressure_type"] == "service reduction"
    assert "pantry closure" in candidate["pressure_summary"]
    assert candidate["evidence_text"] == passage
    assert candidate["eligible_for_review"] is True


def test_supplied_historical_alert_shape_dry_runs_without_mutation():
    source = Path("data/agent-history-staging/food-line/2026-07-28-food-line-source-watch.txt")
    before = source.read_bytes()
    result = process(Path("."), source, edition_date="2026-07-28", agent_name="Food Line Source Watch", agent_run_id="food-line-source-watch-20260728T194707Z-lacalfresh", dry_run=True)
    assert result["would_write"] is False
    assert source.read_bytes() == before
    assert Path("data/agent-history").exists()  # pre-existing private archive remains untouched
