from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_agent_export import export_food_line_agent_run
from bluefern_dispatches.food_line_current_intake import process_batch
from bluefern_dispatches.food_line_current_review import PRIVATE_AGENT_INBOX_ROOT, PRIVATE_QUEUE_PATH
from bluefern_dispatches.food_line_discovery_expansion import build_parser, run_food_line_discovery_expansion
from scripts.import_food_line_agent_findings import validate_input


DATE = "2026-08-01"
STAMP = "2026-08-01T08:00:00Z"
RUN_ID = "food-line-source-watch-20260801T080000Z-test"


def _candidate(**overrides):
    row = {
        "candidate_id": "discovery-current-001",
        "discovery_date": DATE,
        "classification_status": "qualified_pressure_signal",
        "public_claim_eligible": True,
        "public_claim_blockers": [],
        "duplicate_of": "",
        "source_url": "https://example.org/news/pantry-closes",
        "canonical_url": "https://example.org/news/pantry-closes",
        "discovered_publisher": "Example News",
        "source_published_date": DATE,
        "selected_title": "Local pantry closes after supply loss",
        "evidence_text": "The closed pantry left clients without food after losing its remaining supply.",
        "evidence_text_basis": "page_text_excerpt",
        "summary_or_snippet": "A local food-access point closed after losing supply.",
        "metro": "Example City",
        "state_abbrev": "CA",
        "location_scope": "city",
        "affected_groups": ["pantry clients"],
        "pressure_signal": True,
        "pressure_type": "service reduction",
        "confidence": "high",
        "source_role": "local_news_report",
        "evidence_level": "direct reporting",
        "traceability_status": "traceable",
        "discovery_query": "food pantry closure",
        "query_family": "pressure",
        "discovery_channel": "direct_rss",
    }
    row.update(overrides)
    return row


def _export(tmp_path: Path, rows, **kwargs):
    return export_food_line_agent_run(
        list(rows), edition_date=DATE, destination=tmp_path / PRIVATE_AGENT_INBOX_ROOT,
        started_at=STAMP, completed_at="2026-08-01T08:01:00Z", agent_run_id=RUN_ID,
        **kwargs,
    )


def test_valid_one_and_multiple_finding_exports_are_importer_compatible(tmp_path: Path):
    first = _export(tmp_path, [_candidate()])
    payload = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
    assert first["status"] == "success" and first["finding_count"] == 1
    assert validate_input(Path(first["path"]))["valid"] is True
    assert payload["schema_version"] == "food_line_agent_run_v1"
    assert payload["findings"][0]["agent_query_context"]["discovery_candidate_id"] == "discovery-current-001"

    second = _export(tmp_path, [_candidate(), _candidate(candidate_id="discovery-current-002", source_url="https://example.org/news/second", canonical_url="https://example.org/news/second", selected_title="Second pantry closure")], run_slug="multiple")
    assert second["finding_count"] == 2
    assert validate_input(Path(second["path"]))["valid"] is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"evidence_text": ""}, "missing_exact_supporting_passage"),
        ({"source_published_date": ""}, "missing_source_publication_timestamp"),
        ({"source_url": "http://example.org/story", "canonical_url": "http://example.org/story"}, "invalid_or_missing_https_url"),
    ],
)
def test_invalid_findings_are_excluded_without_invalidating_run(tmp_path: Path, overrides, reason):
    result = _export(tmp_path, [_candidate(**overrides)])
    assert result["status"] == "no_exportable_findings"
    assert result["exclusions"][0]["reason"] == reason
    assert result["path"] is None


def test_mixed_export_keeps_valid_finding_and_reports_exclusion(tmp_path: Path):
    result = _export(tmp_path, [_candidate(), _candidate(candidate_id="weak", source_url="https://example.org/weak", canonical_url="https://example.org/weak", evidence_text="")])
    assert result["status"] == "success_with_exclusions"
    assert result["finding_count"] == 1 and result["excluded_count"] == 1


def test_empty_run_returns_durable_audit_result_without_inbox_file(tmp_path: Path):
    result = _export(tmp_path, [])
    assert result["status"] == "no_exportable_findings"
    assert result["path"] is None and result["mutation"] == "none"


def test_identical_export_is_idempotent_and_collision_is_safe(tmp_path: Path):
    first = _export(tmp_path, [_candidate()])
    second = _export(tmp_path, [_candidate()])
    collision = _export(tmp_path, [_candidate(summary_or_snippet="Materially changed factual summary.")])
    assert second["status"] == "idempotent_existing"
    assert second["path"] == first["path"] and second["sha256"] == first["sha256"]
    assert collision["path"] != first["path"]
    assert Path(collision["path"]).name.endswith(f"-{collision['sha256'][:12]}.json")


def test_atomic_failure_leaves_no_partial_file(tmp_path: Path, monkeypatch):
    import bluefern_dispatches.food_line_agent_export as module
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("synthetic rename failure")))
    with pytest.raises(OSError, match="synthetic rename failure"):
        _export(tmp_path, [_candidate()])
    inbox = tmp_path / PRIVATE_AGENT_INBOX_ROOT
    assert not list(inbox.glob("*.json")) and not list(inbox.glob("*.tmp"))


def test_historical_destination_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="historical paths"):
        export_food_line_agent_run([_candidate()], edition_date=DATE, destination=tmp_path / "data/agent-history/food-line", started_at=STAMP, agent_run_id=RUN_ID)


def test_end_to_end_private_smoke_and_rerun(tmp_path: Path):
    result = _export(tmp_path, [_candidate(), _candidate(candidate_id="weak", source_url="https://example.org/weak", canonical_url="https://example.org/weak", evidence_text="")])
    exported = Path(result["path"])
    assert validate_input(exported)["valid"] is True
    dry = process_batch(tmp_path, edition_date=DATE, inbox=exported.parent, build_review_queue=True, build_proposed=True, dry_run=True)
    assert dry["dry_run_count"] == 1 and not (tmp_path / "data/dispatches/food-line/agent-intake").exists()
    first = process_batch(tmp_path, edition_date=DATE, inbox=exported.parent, build_review_queue=True, build_proposed=True)
    second = process_batch(tmp_path, edition_date=DATE, inbox=exported.parent, build_review_queue=True, build_proposed=True)
    queue = json.loads((tmp_path / PRIVATE_QUEUE_PATH).read_text(encoding="utf-8"))
    proposal = json.loads((tmp_path / "data/dispatches/food-line/review/proposed-editions/2026-08-01.json").read_text(encoding="utf-8"))
    assert first["import_count"] == 1 and second["import_count"] == 0 and second["discovered_file_count"] == 0
    assert len(queue["items"]) == 1 and proposal["draft_status"] == "draft_pending_editorial_review"
    assert not (tmp_path / "output/site").exists()
    assert not any((tmp_path / name).exists() for name in ("bluefern-dispatches-pages", "audio", "maps", "podcast.xml"))


def test_runner_flags_are_explicit_and_unscheduled():
    args = build_parser().parse_args(["--date", DATE, "--export-agent-inbox", "--agent-inbox-dir", "private-inbox"])
    assert args.export_agent_inbox is True and args.agent_inbox_dir == "private-inbox"


def test_source_watch_zero_run_records_export_result_without_empty_inbox_file(tmp_path: Path):
    result = run_food_line_discovery_expansion(
        tmp_path, DATE, fetcher=lambda _url: b"", max_queries=0, export_agent_inbox=True,
    )
    audit = json.loads((tmp_path / "output/review/food-line/2026-08-01/discovery_audit.json").read_text(encoding="utf-8"))
    assert result["agent_inbox_export"]["status"] == "no_exportable_findings"
    assert audit["agent_inbox_export"]["mutation"] == "none"
    assert not list((tmp_path / PRIVATE_AGENT_INBOX_ROOT).glob("*.json"))
